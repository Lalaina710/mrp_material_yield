# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    # Marqueur declaratif du dechet de transformation.
    #
    # Ce champ est porte par CE module et non repris de
    # `mrp_byproduct_costshare_sale_price.exclude_from_mrp_byproduct_costshare`
    # pour deux raisons independantes :
    #
    # 1. Semantique differente. Le flag de l'autre module signifie "ce
    #    sous-produit ne participe pas a la repartition de cout", et son propre
    #    texte d'aide precise "Do NOT exclude waste/scrap from sorting - they
    #    must participate with their residual value". Un dechet doit donc etre
    #    inclus dans le cost share tout en etant un dechet : les deux notions
    #    sont orthogonales et ne peuvent pas partager un booleen.
    # 2. Couplage. Reprendre ce champ imposerait une dependance de
    #    `mrp_material_yield` vers `mrp_byproduct_costshare_sale_price`, alors
    #    que le rendement matiere doit pouvoir se calculer sans le module de
    #    valorisation.
    #
    # La detection par nom de categorie ('dechet' / 'déchet' / 'perte') est
    # ecartee : elle depend de l'orthographe, des accents, de la langue et du
    # renommage d'une categorie.
    is_waste_byproduct = fields.Boolean(
        string="Sous-produit déchet",
        default=False,
        help="Si coche, les sous-produits de cette categorie sont comptes "
             "comme dechets dans le rendement matiere des ordres de "
             "fabrication.\n\n"
             "Le dechet reste compte dans le rendement global (c'est de la "
             "matiere sortie, pas de la perte) mais il est exclu du "
             "'rendement utile'.\n\n"
             "Sans rapport avec 'Exclude from Byproduct Cost Share', qui "
             "concerne la répartition de coût et non la nature du "
             "sous-produit.",
    )
