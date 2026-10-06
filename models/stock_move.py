# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools import float_is_zero


class StockMove(models.Model):
    _inherit = "stock.move"

    # --- Rendement de la ligne -----------------------------------------
    # NON STOCKE, contrairement aux champs de `mrp.production`. Trois raisons,
    # dans l'ordre d'importance :
    #
    # 1. `stock_move` est la table la plus volumineuse d'une base Odoo de
    #    production. Un champ stocke y impose un ALTER TABLE puis un recalcul
    #    integral au moment de l'upgrade du module (models.py:3468-3481
    #    selectionne TOUS les enregistrements de la table sans filtre), sur une
    #    base vivante. Le risque de blocage est disproportionne.
    # 2. Le benefice serait nul : ce chiffre ne sert qu'a l'affichage dans
    #    l'onglet Sous-produits. Aucun filtre ni agregat serveur n'en depend --
    #    les agregats viennent des champs stockes de `mrp.production`, et
    #    l'agregat de la vue liste est calcule cote client a partir des
    #    enregistrements deja charges (list_renderer.js:628).
    # 3. Le denominateur est une propriete de l'OF parent, pas du move. Le
    #    stocker sur le move dupliquerait une donnee deja stockee ailleurs,
    #    avec un risque de desynchronisation exactement du type de celui qu'on
    #    corrige entre le dashboard et le rapport PDF.
    #
    # Pas d'attribut `digits` : voir la note detaillee sur les pourcentages
    # dans mrp_production.py. Un `digits=(16, 2)` arrondirait la valeur dans le
    # cache ORM, et l'agregat `sum` de la vue liste additionnerait 7 valeurs
    # deja arrondies -- 97,43 au lieu des 97,44 de l'en-tete.
    yield_pct = fields.Float(
        string="Rendement",
        compute="_compute_yield_pct",
        readonly=True,
        help="Quantité de cette ligne rapportée au total des composants "
             "consommés par l'ordre de fabrication.",
    )
    yield_parent_state = fields.Selection(
        related="production_id.yield_state",
        string="État du rendement de l'OF",
        readonly=True,
        help="Champ technique : pilote le masquage de la colonne Rendement "
             "quand le rendement de l'OF n'est pas calculable.",
    )

    @api.depends(
        # Le denominateur est LU sur l'OF parent, il n'est pas recalcule ici.
        # C'est ce qui garantit qu'il ne peut pas diverger de `yield_input_qty`
        # : il n'existe qu'une seule sommation des composants dans le module.
        "production_id.yield_input_qty",
        "production_id.yield_uom_id",
        "production_id.yield_state",
        "raw_material_production_id.yield_input_qty",
        "raw_material_production_id.yield_uom_id",
        "raw_material_production_id.yield_state",
        # Le numerateur, via le meme helper que l'OF.
        "quantity",
        "product_uom_qty",
        "picked",
        "state",
        "product_uom",
    )
    def _compute_yield_pct(self):
        """Part de ce move dans la matiere engagee par l'OF parent.

        Meme denominateur que `mrp.production.yield_input_qty` -- il est lu,
        pas recalcule -- et meme regle de quantite par move, via
        `_get_yield_move_qty`. Les pourcentages des lignes sous-produits
        somment donc exactement au `material_yield` de l'en-tete.
        """
        # Cache des UdM de reference, partage par tout le lot : une entree par
        # categorie rencontree, pas une requete par ligne de l'onglet.
        uom_cache = {}

        for move in self:
            # Un move composant porte `raw_material_production_id`, un move
            # sous-produit ou produit fini porte `production_id`.
            production = move.production_id or move.raw_material_production_id
            if (
                not production
                # `over_100` inclus : l'OF est en anomalie, mais c'est
                # justement le detail par ligne qui permet de trouver laquelle
                # des quantites a ete mal saisie.
                or production.yield_state not in ("ok", "approx", "over_100")
                or move.state == "cancel"
            ):
                move.yield_pct = 0.0
                continue

            denominator = production.yield_input_qty
            rounding = production.yield_uom_id.rounding or 0.001
            if float_is_zero(denominator, precision_rounding=rounding):
                move.yield_pct = 0.0
                continue

            # L'UdM cible est celle de la categorie DU MOVE, pas
            # `production.yield_uom_id`. En mode `approx` l'OF affiche des kg
            # alors qu'une ligne peut etre en litres : la ramener de force en
            # kg passerait par le repli `raise_if_failure=False` de
            # `_compute_quantity`, ce qui marcherait par accident. On applique
            # explicitement la meme regle que le denominateur.
            target_uom = production._get_yield_move_ref_uom(move, uom_cache)
            qty = production._get_yield_move_qty(move, target_uom)
            move.yield_pct = qty / denominator * 100.0
