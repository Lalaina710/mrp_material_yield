# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    # Source unique de verite pour les champs numeriques du rendement.
    # `_compute_material_yield` les assigne nommement sur le chemin nominal ;
    # `_set_yield_undefined` DERIVE sa neutralisation de ces deux tuples. Un
    # champ ajoute ici est donc automatiquement remis a zero par les
    # garde-fous, sans intervention. Ne pas remplacer par un zip positionnel :
    # reordonner un tuple interchangerait silencieusement deux valeurs.
    _YIELD_QTY_FIELDS = (
        "yield_input_qty",
        "yield_output_qty",
        "yield_waste_qty",
        "yield_useful_qty",
        "yield_loss_qty",
    )
    _YIELD_PCT_FIELDS = (
        "material_yield",
        "yield_waste_pct",
        "yield_useful_pct",
        "yield_loss_pct",
    )
    # Les 11 champs stockes calcules par `_compute_material_yield` : les 9
    # numeriques + les 2 non numeriques. Ensemble verifie contre le registre
    # ORM par `test_00_declared_fields_match_constants`. Sert au verrou
    # d'ecriture de `create` / `write` ci-dessous.
    _YIELD_COMPUTED_FIELDS = _YIELD_QTY_FIELDS + _YIELD_PCT_FIELDS + (
        "yield_uom_id",
        "yield_state",
    )

    # ------------------------------------------------------------------
    # Champs
    # ------------------------------------------------------------------
    # Tous stockes : decision metier explicite. Le stockage permet de filtrer
    # dans la vue liste ("rendement < 90 %") et d'exploiter la donnee depuis
    # Metabase sans reimplementer le calcul en SQL.
    yield_input_qty = fields.Float(
        string="Matière engagée",
        digits="Product Unit of Measure",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Somme des quantités de composants réellement consommés, "
             "convertie dans l'unité de référence de la catégorie d'UdM.",
    )
    yield_output_qty = fields.Float(
        string="Matière sortie",
        digits="Product Unit of Measure",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Somme des quantités de sous-produits réellement produits "
             "(déchets inclus : c'est de la matière sortie, pas de la perte).",
    )
    yield_waste_qty = fields.Float(
        string="Déchets",
        digits="Product Unit of Measure",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Somme des sous-produits dont la catégorie de produit est "
             "cochée « Sous-produit déchet ».\n\n"
             "Vaut 0 tant qu'aucune catégorie n'est cochée : dans ce cas le "
             "« rendement utile » est égal au rendement global et ne mesure "
             "rien de plus.",
    )
    yield_useful_qty = fields.Float(
        string="Matière utile",
        digits="Product Unit of Measure",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Somme des sous-produits hors déchets.",
    )
    yield_loss_qty = fields.Float(
        string="Perte matière",
        digits="Product Unit of Measure",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Matière engagée moins matière sortie.",
    )
    # --- Pourcentages ---------------------------------------------------
    # ATTENTION : ces champs n'ont deliberement PAS d'attribut `digits`.
    #
    # `Float.convert_to_cache` (fields.py:1655-1659) applique
    # `float_round(value, precision_digits=digits[1])` DES QU'UN `digits` EST
    # DEFINI, que le champ soit stocke ou non. Un `digits=(16, 2)` n'est donc
    # pas un parametre de formatage : il arrondit la valeur au niveau de l'ORM,
    # avant que le client web ne la voie.
    #
    # Consequence si on arrondissait ici : l'agregat `sum` d'une vue liste
    # additionne les valeurs BRUTES des enregistrements puis formate le total
    # (list_renderer.js:628 puis 631-641). Sur les 7 sous-produits de
    # US/MO/01877, la somme des 7 pourcentages pre-arrondis vaut 97,43 alors
    # que le rendement d'en-tete vaut 97,44. C'est precisement la derive de
    # 0,01 point a eviter.
    #
    # Sans `digits`, la colonne est `double precision` (fields.py:1624) et le
    # cache garde 97.43530499075786. L'arrondi a 2 decimales est fait
    # uniquement a l'affichage, par `digits="[16, 2]"` sur le noeud de vue
    # (float_field.js:126-131).
    material_yield = fields.Float(
        string="Rendement",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Matière sortie / matière engagée, en pourcentage. Déchets "
             "inclus.\n\n"
             "Valeur stockée en pleine précision : l'arrondi à 2 décimales "
             "est fait à l'affichage.\n\n"
             "⚠ Ne pas recalculer ce chiffre à partir des autres "
             "pourcentages affichés : chacun est juste à sa précision, mais "
             "leur somme ne l'est pas. Sur un cas réel, 93,85 (utile) + 3,58 "
             "(déchets) donne 97,43 alors que le rendement global vaut 97,44. "
             "Ce n'est pas une incohérence, c'est l'arrondi de chaque part.",
    )
    yield_waste_pct = fields.Float(
        string="Déchets %",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Déchets / matière engagée, en pourcentage.\n\n"
             "⚠ « Déchets » + « Rendement utile » ne redonnent pas "
             "exactement « Rendement » une fois arrondis : voir l'aide du "
             "champ Rendement.",
    )
    yield_useful_pct = fields.Float(
        string="Rendement utile",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Matière utile (hors déchets) / matière engagée, en "
             "pourcentage. C'est la part vendable de la matière sortie.\n\n"
             "⚠ « Rendement utile » + « Déchets » ne redonnent pas exactement "
             "« Rendement » une fois arrondis : voir l'aide du champ "
             "Rendement.",
    )
    yield_loss_pct = fields.Float(
        string="Perte",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Perte matière / matière engagée, en pourcentage. "
             "« Rendement » + « Perte » valent 100 %.",
    )
    yield_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UdM du rendement",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        help="Unité de référence dans laquelle les quantités du rendement "
             "sont exprimées. Catégorie d'UdM majoritaire de l'OF.",
    )
    yield_state = fields.Selection(
        selection=[
            ("ok", "Calculé"),
            ("approx", "Approximatif (poids + volume)"),
            ("over_100", "Anomalie : supérieur à 100 %"),
            ("no_byproduct", "Aucun sous-produit"),
            ("uom_mixed", "Unités incompatibles"),
            ("no_input", "Aucun composant consommé"),
        ],
        string="État du rendement",
        compute="_compute_material_yield",
        store=True,
        readonly=True,
        default="no_input",
        help="Indique si le rendement est exploitable.\n\n"
             "« Calculé » et « Approximatif » portent une valeur agrégeable. "
             "« Approximatif » signifie que des poids et des volumes ont été "
             "additionnés sans conversion.\n\n"
             "« Anomalie : supérieur à 100 % » porte aussi une valeur, "
             "affichée en rouge, mais l'OF est exclu des moyennes : sortir "
             "plus de matière qu'on en engage révèle une erreur de saisie.\n\n"
             "⚠ Le seuil de cette anomalie est RELATIF à la précision de "
             "saisie, pas absolu : il vaut l'arrondi de l'unité multiplié par "
             "le nombre de mouvements. Deux OF affichant tous deux 100,11 % "
             "peuvent donc être classés différemment — un OF à 3 lignes et "
             "quelques kilos est intrinsèquement plus bruité qu'un OF à "
             "12 lignes et une tonne.\n\n"
             "Les autres états signifient que le pourcentage n'a pas de sens "
             "et ne doit pas être présenté à l'utilisateur.",
    )
    # Purement presentationnel : non stocke, aucun interet en reporting.
    yield_warning = fields.Char(
        string="Alerte rendement",
        compute="_compute_yield_warning",
        help="Message affiché lorsque le rendement est approximatif, "
             "anormal, ou non calculable. Vide dans le cas nominal.",
    )

    # ------------------------------------------------------------------
    # Verrou d'ecriture des champs calcules
    # ------------------------------------------------------------------
    # `readonly=True` ne protege que l'interface : par RPC, un utilisateur
    # MRP peut ecrire ces colonnes et fausser le KPI direction jusqu'au
    # prochain recalcul. On refuse donc toute ecriture explicite, y compris
    # pour l'administrateur : ces valeurs sont calculees, personne ne doit
    # les saisir.
    #
    # Aucun contexte d'exception n'est necessaire, verifie sur le source 18 :
    #   - le recalcul ORM passe par `Field.__set__` sur des enregistrements
    #     proteges -> `Field.write` (cache + dirty, fields.py:1382-1398), puis
    #     `_flush` -> `_write_multi` (models.py:6844) ; jamais `write()` ;
    #   - le backfill d'installation / upgrade emprunte ce meme chemin ;
    #   - `copy()` / `_split_productions` passent par `copy_data`, qui ignore
    #     les champs `copy=False` -- defaut d'un champ calcule readonly
    #     (fields.py:445-447) ;
    #   - les valeurs par defaut (`yield_state`) sont ajoutees par
    #     `_add_missing_default_values`, en aval de ce `create` ;
    #   - le client web n'envoie pas les champs readonly sans `force_save`
    #     (record.js:635-641) ;
    #   - ni ce module, ni sa migration n'ecrivent ces champs via `write()`.
    def _check_yield_fields_not_written(self, vals_list):
        written = {
            name for vals in vals_list for name in vals
            if name in self._YIELD_COMPUTED_FIELDS
        }
        if not written:
            return
        labels = ", ".join(
            f"« {self._fields[name]._description_string(self.env)} »"
            for name in self._YIELD_COMPUTED_FIELDS
            if name in written
        )
        raise UserError(_(
            "Les champs de rendement matière sont calculés automatiquement à "
            "partir des mouvements de l'ordre de fabrication et ne peuvent pas "
            "être saisis : %(fields)s.",
            fields=labels,
        ))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_yield_fields_not_written(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_yield_fields_not_written([vals])
        return super().write(vals)

    # ------------------------------------------------------------------
    # Calcul
    # ------------------------------------------------------------------
    # NOTE IMPORTANTE sur le @api.depends ci-dessous.
    #
    # On NE depend PAS de `move_byproduct_ids`, alors que c'est le champ que
    # l'on veut mesurer. Raison, verifiee dans le source Odoo 18 :
    #
    #   mrp/models/mrp_production.py:188
    #     move_byproduct_ids = fields.One2many('stock.move',
    #         compute='_compute_move_byproduct_ids', inverse=...)
    #
    # Ce champ n'a ni `store=True`, ni `search=`, ni `inverse_name`. Donc :
    #   - `Field._description_searchable` (fields.py:896) renvoie False ;
    #   - `registry.field_inverses[move_byproduct_ids]` est vide ;
    #   - au runtime, `_modified_triggers` (models.py:7304-7309) tombe dans la
    #     branche `else` et execute
    #         mrp.production.search([('move_byproduct_ids', 'in', move_ids)])
    #   - or `expression.py:1166-1174` traite un champ non stocke sans methode
    #     `search` en loguant une ERREUR *puis en generant une feuille vide* :
    #     le domaine devient `[]`.
    #
    # Consequence : un `@api.depends('move_byproduct_ids.quantity')` sur un
    # champ STOCKE ferait recalculer ce champ sur TOUS les OF de la base a
    # chaque ecriture sur un stock.move de sous-produit. Silencieusement.
    #
    # On depend donc de `move_finished_ids` (stocke, inverse_name
    # 'production_id' => trigger resolu par jointure directe) et on refait
    # le filtre sous-produit nous-memes, a l'identique du core :
    #   mrp_production.py:695 (core deploye 45)
    #     move_finished_ids.filtered(lambda m: m.product_id != order.product_id)
    @api.depends(
        # Le produit fini de l'OF : c'est lui qui partitionne move_finished_ids
        # entre "produit fini" et "sous-produits". S'il change, la partition
        # change, donc le numerateur change.
        "product_id",
        # Etat de l'OF. Strictement redondant avec move_*.state dans le flux
        # normal (confirm/produce/cancel ecrivent sur les moves), mais conserve
        # comme filet : garantit un recalcul sur toute transition d'etat, y
        # compris celles ou les moves etaient deja dans l'etat cible.
        "state",
        # --- Composants (denominateur) ---
        # Le champ nu : ajout / retrait / remplacement d'une ligne composant.
        "move_raw_ids",
        # L'exclusion des moves annules depend de l'etat.
        "move_raw_ids.state",
        # `picked` arbitre entre quantite reelle et quantite planifiee.
        "move_raw_ids.picked",
        # La quantite reelle.
        "move_raw_ids.quantity",
        # La quantite planifiee, utilisee tant que le move n'est pas picked.
        "move_raw_ids.product_uom_qty",
        # L'UdM : elle pilote a la fois le garde-fou de categorie et la
        # conversion vers l'unite de reference.
        "move_raw_ids.product_uom",
        # --- Sous-produits (numerateur), via move_finished_ids ---
        "move_finished_ids",
        "move_finished_ids.state",
        "move_finished_ids.picked",
        "move_finished_ids.quantity",
        "move_finished_ids.product_uom_qty",
        "move_finished_ids.product_uom",
        # Necessaire ici et pas cote composants : le filtre sous-produit
        # compare `move.product_id` au produit de l'OF. Un move qui change de
        # produit bascule entre produit fini et sous-produit.
        "move_finished_ids.product_id",
        # Partition dechet / utile. Chaine entierement reversible :
        # `product.product.categ_id` est un champ delegue (_inherits) dont la
        # cible `product.template.categ_id` est stockee ; `setup_related`
        # (fields.py:626-628) lui attribue alors `search = _search_related`,
        # donc il est searchable. C'est ce qui manque a `move_byproduct_ids`.
        "move_finished_ids.product_id.categ_id.is_waste_byproduct",
        # Flag d'assimilation au poids. INDISPENSABLE : sans lui, cocher la
        # case sur une categorie ne recalculerait aucun OF et le rendement
        # resterait fige sur `uom_mixed`. Chaine reversible de bout en bout,
        # `product_uom` (m2o stocke) -> `category_id` (m2o stocke) ->
        # `yield_mass_equivalent` (booleen stocke).
        "move_raw_ids.product_uom.category_id.yield_mass_equivalent",
        "move_finished_ids.product_uom.category_id.yield_mass_equivalent",
    )
    def _compute_material_yield(self):
        """Calcule le rendement matiere reel de l'OF.

        Rendement = somme(sous-produits sortis) / somme(composants consommes).

        Le produit fini de l'OF est exclu du numerateur : chez SOPROMER c'est
        un produit-operation fictif dont la quantite est saisie a la main, donc
        le ratio natif `qty_produced / product_qty` vaut 100 % par
        construction. Les dechets, eux, RESTENT dans le numerateur : c'est de
        la matiere sortie, pas de la perte.
        """
        # Cache local des UdM de reference, une entree par categorie
        # rencontree. Evite une requete par OF lors d'un recalcul de masse
        # (installation du module, upgrade).
        reference_uom_cache = {}

        approx_categories = self._get_yield_approx_categories()

        for production in self:
            raw_moves, byproduct_moves = production._get_yield_moves()

            # --- Garde-fou 1 : pas de composant du tout ---
            if not raw_moves:
                production._set_yield_undefined("no_input")
                continue

            # --- Garde-fou 2 : pas de sous-produit ---
            # Cas reel US/MO/00090. On refuse d'ecrire 0 %, qui se lirait
            # comme un incident de production alors qu'il n'y a simplement
            # rien a mesurer.
            if not byproduct_moves:
                production._set_yield_undefined("no_byproduct")
                continue

            # --- Garde-fou 3 : coherence des unites ---
            # Additionner des kg et des pieces ne veut rien dire. Trois cas,
            # selon les categories d'UdM presentes sur les moves retenus.
            categories = (raw_moves | byproduct_moves).product_uom.category_id
            if len(categories) == 1:
                # Une seule categorie : conversion intra-categorie vers l'UdM
                # de reference, exacte.
                state = "ok"
            elif categories <= approx_categories:
                # Toutes les categories presentes sont declarees assimilables
                # au poids (`uom.category.yield_mass_equivalent`). On additionne
                # par exemple des litres a des kilogrammes, ce qui est faux au
                # sens strict mais dont l'erreur est BORNEE tant que le flag
                # n'est pose que sur des grandeurs de densite voisine de 1 :
                # densite 1 pour l'eau (erreur nulle), ~0,92 pour l'huile
                # (~0,14 kg sur 1,7 L). C'est aussi la convention de saisie
                # maison -- le `product_qty` du pivot vaut exactement cette
                # somme naive -- et l'ecran le signale comme approximatif.
                state = "approx"
            else:
                # Au moins une categorie n'est pas declaree assimilable. Le cas
                # a proscrire est celui des Unites, ou l'erreur est NON BORNEE :
                # 3 sachets peuvent peser 75 g ou 15 kg. Aucun calcul, aucun
                # pourcentage affiche.
                production._set_yield_undefined("uom_mixed")
                continue

            # L'UdM d'affichage est celle de la categorie majoritaire en
            # nombre de moves : sur une recette boulettes c'est le kg, pas le
            # litre. Determine avant les sommes car `yield_loss_qty` s'y
            # exprime.
            target_uom = production._get_yield_display_uom(
                raw_moves | byproduct_moves, reference_uom_cache
            )

            input_qty = sum(
                self._get_yield_move_qty(
                    move, self._get_yield_move_ref_uom(move, reference_uom_cache)
                )
                for move in raw_moves
            )

            # Partition dechet / utile. Le flag est porte par la categorie de
            # produit (`product.category.is_waste_byproduct`), pas devine a
            # partir du nom : un test du genre `'dechet' in categ.name` casse
            # au premier renommage, au premier accent et au premier libelle
            # anglais.
            waste_moves = byproduct_moves.filtered(
                lambda m: m.product_id.categ_id.is_waste_byproduct
            )
            useful_moves = byproduct_moves - waste_moves

            waste_qty = sum(
                self._get_yield_move_qty(
                    move, self._get_yield_move_ref_uom(move, reference_uom_cache)
                )
                for move in waste_moves
            )
            useful_qty = sum(
                self._get_yield_move_qty(
                    move, self._get_yield_move_ref_uom(move, reference_uom_cache)
                )
                for move in useful_moves
            )
            # Somme des deux parts plutot qu'une troisieme sommation sur
            # `byproduct_moves` : additionner les memes flottants dans un ordre
            # different peut differer d'un ulp. On garantit ainsi exactement
            # output = utile + dechet, donc
            # input = utile + dechet + perte -- invariant sur lequel le
            # dashboard s'appuie pour recomposer ses pourcentages.
            output_qty = useful_qty + waste_qty

            # --- Garde-fou 4 : division par zero ---
            # Des moves composants existent mais aucun n'a de quantite (non
            # picked et sans demande, quantites saisies a zero...). On compare
            # avec la precision de l'UdM plutot qu'avec `not input_qty` : un
            # residu flottant de 1e-15 passerait le test de verite et
            # produirait un rendement aberrant. Un total negatif (retours mal
            # saisis) est traite pareil : un rendement negatif n'a pas de sens.
            rounding = target_uom.rounding or 0.001
            if input_qty < 0 or float_is_zero(input_qty, precision_rounding=rounding):
                production._set_yield_undefined("no_input")
                continue

            loss_qty = input_qty - output_qty

            # --- Garde-fou 5 : rendement physiquement impossible ---
            # Sortir plus de matiere qu'on en a engage n'existe pas. C'est une
            # anomalie de saisie (US/MO/00441 : 12,000 entres, 19,720 sortis,
            # soit 164 %). Ces OF passaient en 'ok' et gonflaient la moyenne du
            # dashboard.
            #
            # Le seuil n'est PAS un epsilon arbitraire : on compare avec la
            # precision de l'UdM multipliee par le nombre de moves sommes.
            # Chaque move apporte au plus un arrondi d'UdM, donc le bruit
            # cumule d'une somme de N moves est borne par `rounding * N` -- soit
            # 0,008 kg pour 8 mouvements a 0,001, environ 0,01 % sur un OF de
            # 86 kg. Un ecart superieur n'est plus du bruit.
            #
            # A ce compte US/MO/00450 (100,11 %) reste une vraie anomalie :
            # 0,11 % de matiere en trop depasse largement le plancher de bruit.
            #
            # Base sur l'arrondi de l'UdM plutot que sur une constante en dur :
            # reste juste si des produits arrivent un jour avec une precision
            # differente, et s'explique en une phrase a l'utilisateur.
            noise_floor = rounding * max(len(raw_moves) + len(byproduct_moves), 1)
            # On compare l'ECART au seuil, et non les deux valeurs entre elles.
            # `float_compare(a, b, precision_rounding=X)` arrondit a et b sur
            # une grille de pas X puis compare : sa propre docstring previent
            # que « A value is considered lower/greater than another value if
            # their rounded value is different. This is not the same as having
            # a non-zero difference! ». Snapper input et output sur une grille
            # de pas 0,008 ne teste pas « l'excedent depasse-t-il 0,008 » : le
            # verdict depend de la position des deux valeurs sur la grille, pas
            # de leur difference.
            if float_compare(
                output_qty - input_qty, noise_floor, precision_rounding=rounding
            ) > 0:
                # La valeur reste calculee et affichee : c'est un signal
                # d'exploitation, la masquer priverait l'utilisateur du seul
                # ecran ou l'anomalie de saisie est visible. `over_100` prime
                # sur `approx` -- l'impossibilite physique est l'information
                # dominante -- mais le message d'avertissement mentionne les
                # deux quand les deux s'appliquent.
                state = "over_100"

            # Aucun arrondi ici : les valeurs assignees sont les flottants
            # complets (97.43530499075786 sur US/MO/01877). L'arrondi a
            # 2 decimales est la responsabilite de la vue. Arrondir des
            # maintenant se propagerait dans tous les agregats en aval.
            #
            # `update` avec un dict nomme plutot que 11 assignations. Le
            # chemin de neutralisation ci-dessous DERIVE sa liste de champs de
            # `_YIELD_QTY_FIELDS` + `_YIELD_PCT_FIELDS`, il ne la reecrit pas :
            # un champ ajoute a ces constantes est donc couvert d'office cote
            # garde-fou. S'il manque ici, c'est le chemin NOMINAL qui leve
            # `Compute method failed to assign`, donc le premier OF venu et
            # n'importe quel test -- pas un cas de bord a 14 OF sur 1824.
            #
            # Volontairement nomme et non `zip(constantes, valeurs)` : un zip
            # positionnel intervertit silencieusement deux valeurs le jour ou
            # quelqu'un reordonne la constante.
            production.update({
                "yield_input_qty": input_qty,
                "yield_output_qty": output_qty,
                "yield_waste_qty": waste_qty,
                "yield_useful_qty": useful_qty,
                "yield_loss_qty": loss_qty,
                "material_yield": output_qty / input_qty * 100.0,
                "yield_waste_pct": waste_qty / input_qty * 100.0,
                "yield_useful_pct": useful_qty / input_qty * 100.0,
                "yield_loss_pct": loss_qty / input_qty * 100.0,
                "yield_uom_id": target_uom.id,
                "yield_state": state,
            })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_yield_undefined(self, state):
        """Neutralise tous les champs de rendement avec l'etat donne.

        Un champ stocke doit toujours etre assigne dans un compute, sinon
        Odoo leve `ValueError: Compute method failed to assign`. On met les
        quantites a zero et c'est `yield_state` qui porte l'information ;
        la vue s'appuie sur lui pour ne rien afficher.

        La liste des champs est DERIVEE des constantes de classe, jamais
        reecrite. C'est le point important, et le chemin est loin d'etre
        marginal : il est emprunte par TOUT OF annule (aucun composant
        retenu -> garde-fou 1), soit 132 OF sur 1824 sur le 45 et 133 sur
        1864 sur le 43 -- 7 % de la table. Un champ oublie ici leve
        `ValueError: Compute method failed to assign`, et comme le backfill
        natif recalcule TOUTE la table a l'installation, c'est l'upgrade
        lui-meme qui echoue -- pas seulement l'ouverture d'un formulaire.
        Ne jamais remplacer cette derivation par des assignations en dur.
        """
        vals = dict.fromkeys(
            self._YIELD_QTY_FIELDS + self._YIELD_PCT_FIELDS, 0.0
        )
        vals["yield_uom_id"] = False
        vals["yield_state"] = state
        for record in self:
            record.update(vals)

    def _get_yield_moves(self):
        """(composants, sous-produits) retenus pour le rendement.

        Factorise ici parce que trois appelants ont besoin exactement du meme
        peripherique : le calcul du rendement, le message d'avertissement, et
        le diagnostic d'UdM. Deux filtres divergents finiraient par produire
        deux verites.
        """
        self.ensure_one()
        raw_moves = self.move_raw_ids.filtered(lambda m: m.state != "cancel")
        # Meme filtre que le core `_compute_move_byproduct_ids`
        # (mrp_production.py:695 sur le core deploye 45 ; le numero derive
        # entre point releases, l'ancre fiable est le nom de la methode),
        # applique sur le champ stocke -- PLUS l'exclusion des moves annules,
        # que le core ne fait PAS. Ce n'est donc pas une copie : le core rend
        # un recordset destine a l'affichage, ou un move annule reste visible ;
        # ici on somme des quantites, et un move annule n'a rien consomme ni
        # rien produit. L'ecart est deliberé.
        byproduct_moves = self.move_finished_ids.filtered(
            lambda m, prod=self.product_id: (
                m.state != "cancel" and m.product_id != prod
            )
        )
        return raw_moves, byproduct_moves

    @api.model
    def _get_yield_approx_categories(self):
        """Categories entre lesquelles la somme naive est toleree.

        Lues depuis le flag declaratif `uom.category.yield_mass_equivalent`,
        et non plus resolues en dur par xmlid.

        Pourquoi ce changement : la categorie parasite issue de l'import Sage
        n'a pas d'xmlid (creee par `name_create`), et sa correction dans la
        donnee est IMPOSSIBLE -- `stock/models/product.py:1126-1155` refuse
        tout changement de `category_id` des qu'un `stock.quant` non nul
        existe sur un produit portant l'UdM. Voir le commentaire detaille sur
        `uom.category.yield_mass_equivalent`.

        Les deux categories standard (Poids, Volume) sont cochees par les
        donnees du module, en `noupdate="1"` pour qu'un upgrade ne reecrase
        pas un choix d'administrateur. Toute autre categorie releve d'une
        decision explicite, prise en connaissance du risque documente dans le
        `help=` du champ : l'erreur n'est bornee que pour des grandeurs de
        densite voisine de 1.

        `active_test=False` : une categorie desactivee doit continuer a etre
        reconnue, sinon un OF historique basculerait silencieusement de
        `approx` a `uom_mixed` le jour ou quelqu'un archive une categorie.
        """
        return self.env["uom.category"].with_context(active_test=False).search(
            [("yield_mass_equivalent", "=", True)]
        )

    @api.model
    def _get_yield_move_ref_uom(self, move, cache):
        """UdM de reference de la categorie DU MOVE.

        C'est l'unite dans laquelle la quantite du move entre dans la somme.
        En mode `ok` il n'y a qu'une categorie, donc une seule reference. En
        mode `approx` chaque move est ramene a la reference de SA categorie
        (kg pour Poids, L pour Volume -- `uom_data.xml:88-94` confirme que la
        reference Volume est bien le litre et non le m3), puis les deux sont
        additionnes naivement. La conversion INTRA-categorie reste exacte ;
        seul le franchissement Poids<->Volume est approximatif.
        """
        category = move.product_uom.category_id
        if category.id not in cache:
            cache[category.id] = (
                self._get_yield_reference_uom(category) or move.product_uom
            )
        return cache[category.id]

    def _get_yield_display_uom(self, moves, cache):
        """UdM dans laquelle `yield_loss_qty` et consorts sont exprimes.

        Categorie majoritaire en nombre de moves : sur une recette boulettes
        (4 lignes kg, 2 lignes L) c'est le kg. Afficher une perte en litres
        parce que la premiere ligne est de l'eau serait deroutant.
        """
        self.ensure_one()
        counts = {}
        for move in moves:
            category = move.product_uom.category_id
            counts[category] = counts.get(category, 0) + 1
        if not counts:
            return self.env["uom.uom"]
        # Tri secondaire sur l'id pour que le resultat soit deterministe en
        # cas d'egalite ; sans cela l'UdM affichee pourrait changer d'un
        # recalcul a l'autre.
        dominant = max(counts.items(), key=lambda kv: (kv[1], -kv[0].id))[0]
        if dominant.id not in cache:
            cache[dominant.id] = self._get_yield_reference_uom(dominant)
        return cache[dominant.id] or moves[:1].product_uom

    @api.model
    def _get_yield_reference_uom(self, category):
        """Retourne l'UdM de reference de la categorie (facteur 1).

        `uom.category.reference_uom_id` existe mais est declare `store=False`
        et ne sert que de memoire d'onchange (uom_uom.py:18) : inutilisable
        ici. On cherche donc l'UdM de type 'reference'.

        Renvoie un recordset vide si la categorie n'en a pas : c'est a
        l'appelant de decider du repli.
        """
        reference = self.env["uom.uom"].search(
            [
                ("category_id", "=", category.id),
                ("uom_type", "=", "reference"),
            ],
            limit=1,
        )
        if not reference:
            _logger.warning(
                "Categorie d'UdM %s sans unite de reference : le rendement "
                "sera exprime dans l'UdM du premier composant.",
                category.display_name,
            )
        return reference

    @api.model
    def _get_yield_move_qty(self, move, target_uom):
        """Quantite d'un move, exprimee dans `target_uom`.

        - `picked` vrai  => `quantity`, la quantite reellement traitee.
          En Odoo 18 `stock.move.picked` est un compute stocke qui vaut
          systematiquement True des que `state == 'done'`
          (stock_move.py:261-267), donc sur un OF termine ce cas couvre tout.
        - `picked` faux  => `product_uom_qty`, la demande planifiee. Le move
          n'a rien consomme (stock_move.py:2054 l'exclut de `_action_done`),
          mais sur un OF en cours c'est la seule estimation disponible : la
          valeur est alors provisoire, pas definitive.

        `round=False` est obligatoire : la valeur par defaut de
        `uom.uom._compute_quantity` est `round=True, rounding_method='UP'`
        (uom_uom.py:211), qui arrondirait chaque move a la hausse et gonflerait
        artificiellement les deux sommes.

        `raise_if_failure=False` : la coherence de categorie est deja garantie
        par le garde-fou en amont, cette branche est inatteignable. On evite
        malgre tout qu'une anomalie de donnee fasse planter le formulaire OF.
        """
        qty = move.quantity if move.picked else move.product_uom_qty
        if not qty:
            return 0.0
        if move.product_uom and target_uom and move.product_uom != target_uom:
            qty = move.product_uom._compute_quantity(
                qty, target_uom, round=False, raise_if_failure=False
            )
        return qty

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------
    @api.depends(
        "yield_state",
        "move_raw_ids.product_uom",
        "move_raw_ids.state",
        "move_finished_ids.product_uom",
        "move_finished_ids.state",
        "move_finished_ids.product_id",
        "product_id",
        # Le message nomme les categories fautives et annonce le resultat
        # attendu apres correction : il change quand le flag change.
        "move_raw_ids.product_uom.category_id.yield_mass_equivalent",
        "move_finished_ids.product_uom.category_id.yield_mass_equivalent",
    )
    def _compute_yield_warning(self):
        """Message d'avertissement, non stocke (purement presentationnel).

        Nomme les produits fautifs et leur categorie d'UdM. Un message
        generique du type "unites incompatibles" laisserait l'utilisateur
        devant un OF vide sans moyen de savoir quelle fiche produit corriger :
        c'est le pire des cas, il ne verrait qu'un ecran muet.
        """
        approx_categories = self._get_yield_approx_categories()

        for production in self:
            state = production.yield_state
            if state not in ("approx", "uom_mixed", "over_100"):
                production.yield_warning = ""
                continue

            raw_moves, byproduct_moves = production._get_yield_moves()
            moves = raw_moves | byproduct_moves

            if state == "over_100":
                # `over_100` prime sur `approx` dans `yield_state`, mais si les
                # UdM etaient aussi melangees il faut le dire : c'est peut-etre
                # la cause meme du depassement (des litres comptes comme des
                # kg). Message compose plutot que deux etats concurrents.
                message = _(
                    "Anomalie : %(out)s sortis pour %(inp)s engagés, soit "
                    "%(pct)s %%. Sortir plus de matière qu'on en engage est "
                    "impossible : vérifier les quantités saisies. Cet OF est "
                    "exclu des moyennes de rendement.",
                    out=formatLang(self.env, production.yield_output_qty, digits=3),
                    inp=formatLang(self.env, production.yield_input_qty, digits=3),
                    pct=formatLang(self.env, production.material_yield, digits=2),
                )
                categories = moves.product_uom.category_id
                if len(categories) > 1:
                    culprits = moves.filtered(
                        lambda m, c=production.yield_uom_id.category_id: (
                            m.product_uom.category_id != c
                        )
                    )
                    message += " " + _(
                        "Des unités de catégorie différente ont été "
                        "additionnées, ce qui peut expliquer l'écart : "
                        "%(details)s.",
                        details=production._format_yield_culprits(culprits),
                    )
                production.yield_warning = message
                continue

            if state == "approx":
                # Les fautifs sont ceux qui ne sont PAS dans la categorie
                # d'affichage : ce sont eux qui ont ete additionnes naivement.
                display_uom = production.yield_uom_id
                culprits = moves.filtered(
                    lambda m, c=display_uom.category_id: (
                        m.product_uom.category_id != c
                    )
                )
                production.yield_warning = _(
                    "Rendement approximatif : des quantités de catégorie "
                    "différente ont été additionnées à des %(unit)s. "
                    "Concerné : %(details)s. L'écart reste faible (densité "
                    "proche de 1) mais le chiffre n'est pas exact.",
                    unit=display_uom.name or "",
                    details=production._format_yield_culprits(culprits),
                )
                continue

            # uom_mixed : les fautifs sont ceux hors du peripherique tolere.
            culprits = moves.filtered(
                lambda m, allowed=approx_categories: (
                    m.product_uom.category_id not in allowed
                )
            )
            if not culprits:
                # Cas theorique : plusieurs categories toutes tolerees aurait
                # donne 'approx'. On retombe sur la liste complete plutot que
                # sur un message vide.
                culprits = moves

            # Nommer TOUTES les categories presentes, pas seulement les
            # bloquantes. Sur US/MO/01879 les bloquantes se reduisent a
            # EN_SEL03 (categorie fourre-tout d'import), mais l'OF contient
            # aussi EAU et AU_HUI02 en Volume : un message qui tait ces deux
            # lignes laisse croire que corriger EN_SEL03 rendra le rendement
            # EXACT, alors qu'il deviendra APPROXIMATIF (poids + volume).
            # L'utilisateur doit savoir ce qu'il obtiendra avant de corriger.
            categories = moves.product_uom.category_id
            message = _(
                "Rendement non calculable : les composants et les "
                "sous-produits n'utilisent pas des unités additionnables. "
                "Catégories présentes : %(categories)s.",
                categories=", ".join(sorted(c.name or "" for c in categories)),
            )
            message += " " + _(
                "Bloquant : %(details)s. Corriger la catégorie d'unité de "
                "mesure sur la fiche produit.",
                details=production._format_yield_culprits(culprits),
            )
            # Annonce du resultat attendu apres correction, pour eviter la
            # deception « j'ai corrige et le chiffre est marque approximatif ».
            remaining = categories.filtered(lambda c: c in approx_categories)
            if len(remaining) > 1:
                message += " " + _(
                    "Après correction il restera %(remaining)s : le rendement "
                    "sera alors calculé mais APPROXIMATIF, ces unités étant "
                    "additionnées sans conversion.",
                    remaining=", ".join(
                        sorted(c.name or "" for c in remaining)
                    ),
                )
            production.yield_warning = message

    def _format_yield_culprits(self, moves):
        """'EAU (6,270 L, Volume), AU_HUI02 (1,700 L, Volume)'.

        Limite a 6 entrees : au-dela le message deborde de l'ecran et n'aide
        plus. Le compte restant est indique.
        """
        self.ensure_one()
        parts = []
        for move in moves[:6]:
            qty = move.quantity if move.picked else move.product_uom_qty
            parts.append("%s (%s %s, %s)" % (
                move.product_id.default_code or move.product_id.name,
                formatLang(self.env, qty, digits=3),
                move.product_uom.name or "",
                move.product_uom.category_id.name or "",
            ))
        remaining = len(moves) - 6
        if remaining > 0:
            parts.append(_("et %s autre(s)", remaining))
        return ", ".join(parts)
