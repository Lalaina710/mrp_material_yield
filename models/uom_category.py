# -*- coding: utf-8 -*-
from odoo import fields, models


class UoMCategory(models.Model):
    _inherit = "uom.category"

    # Marqueur declaratif des categories d'UdM assimilables au poids.
    #
    # POURQUOI CE CHAMP EXISTE
    # ------------------------
    # La categorie d'UdM parasite issue de l'import Sage (une unite nommee
    # « kg », facteur 1.0, rangee dans « Unsorted/Imported Units ») NE PEUT PAS
    # etre corrigee dans la donnee. `stock/models/product.py:1126-1155` etend
    # `uom.uom.write()` et refuse tout changement de `category_id` des lors
    # qu'un `stock.quant` non nul existe sur un produit portant cette UdM :
    #
    #     if self.env['stock.quant'].sudo().search_count([
    #         ('product_id.product_tmpl_id.uom_id', 'in', changed.ids),
    #         ('quantity', '!=', 0),
    #     ]):
    #         raise UserError(error_msg)
    #
    # Le garde-fou est inconditionnel : les trois `search_count` sont en
    # `sudo()`, aucune cle de contexte ne les desarme. Il protege le grand
    # livre de stock, et il a raison de le faire. 13 quants non nuls sont
    # concernes, dont du stock reel en entrepot.
    #
    # A noter : le message parle de « ratio », mais la condition d'entree
    # inclut `category_id` -- un simple reclassement declenche donc une erreur
    # qui evoque un changement de facteur. C'est deroutant, et c'est ce qui a
    # fait croire un temps que le blocage etait contournable.
    #
    # Le probleme n'est pas non plus historique : le produit garde cette UdM,
    # donc chaque futur OF utilisant ce composant serait prive de rendement.
    #
    # D'ou ce flag : on ne corrige pas la donnee, on rend le module
    # configurable. Meme patron que `product.category.is_waste_byproduct` --
    # declaratif, explicite, reversible, et sans effet de bord sur le stock.
    #
    # Pas de `groups=` : l'ACL de `uom.category` restreint deja l'ecriture a
    # `base.group_system`, `mrp.group_mrp_manager`,
    # `purchase.group_purchase_manager`, `sales_team.group_sale_manager` et
    # `stock.group_stock_manager` ; `base.group_user` et `mrp.group_mrp_user`
    # sont en lecture seule. Ajouter un groupe ici masquerait le champ a une
    # partie des roles habilites sans rien proteger de plus.
    yield_mass_equivalent = fields.Boolean(
        string="Assimilable au poids (bilan matière)",
        default=False,
        help="Si coché, les unités de cette catégorie peuvent être "
             "additionnées aux kilogrammes dans le calcul du rendement "
             "matière des ordres de fabrication.\n\n"
             "L'ordre de fabrication est alors marqué « Approximatif », "
             "jamais « Calculé » : l'écran signale que le chiffre résulte "
             "d'une somme sans conversion.\n\n"
             "⚠ À réserver aux catégories dont l'écart au poids est BORNÉ. "
             "Pour les volumes, la densité proche de 1 limite l'erreur à "
             "environ 0,3 point de rendement (eau : densité 1, erreur nulle ; "
             "huile : ~0,92). Cocher une catégorie d'Unités produirait au "
             "contraire une erreur NON BORNÉE — 3 sachets peuvent peser 75 g "
             "comme 15 kg — et rendrait le rendement arbitraire.",
    )
