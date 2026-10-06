# -*- coding: utf-8 -*-
{
    'name': 'MRP Material Yield',
    # 1.8.0 : script de migration post-migrate. `data/uom_category_data.xml`
    # est en `noupdate="1"` sur des xmlid appartenant au module `uom` : en
    # mode update, convert.py:341-350 saute purement et simplement le noeud,
    # donc le flag restait NULL sur une base existante.
    # 1.7.0 : `uom.category.yield_mass_equivalent` -- les categories
    # assimilables au poids deviennent configurables au lieu d'etre resolues
    # en dur par xmlid. Rendu necessaire par l'impossibilite de corriger la
    # donnee : stock/models/product.py:1126-1155 refuse tout changement de
    # `category_id` des qu'un stock.quant non nul existe.
    # 1.6.1 : correction d'une assertion de test qui comparait
    # `round(a, 2) + round(b, 2)` a un litteral -- 93.85 + 3.58 vaut
    # 97.42999999999999 en binaire. Le test qui documente le piege des
    # arrondis y etait lui-meme tombe. Aucun changement de code metier.
    # 1.6.0 : correction de `setUpClass` des tests -- l'external ID
    # `stock.stock_location_production` n'existe pas (l'emplacement est cree
    # par societe en Python, res_company.py:80-89, sans xmlid). Les 14 tests
    # echouaient avant d'atteindre leur corps.
    # 1.5.0 : i18n/mrp_material_yield.pot (52 entrees), et correction du
    # `groups=` sur `is_waste_byproduct` -- `mrp.group_mrp_manager` masquait le
    # champ aux SEULS roles pouvant ecrire une categorie de produit.
    # 1.4.0 : tests unitaires (dont controle statique ensemble-contre-ensemble
    # des 11 champs calcules), correction du seuil `over_100` qui comparait
    # deux valeurs snappees sur une grille au lieu de comparer l'ecart au
    # plancher de bruit, message `uom_mixed` nommant toutes les categories,
    # libelles accentues, ACL sur `is_waste_byproduct`.
    # 1.3.0 : etat `over_100` (rendement physiquement impossible, calcule et
    # affiche mais exclu des agregats), seuil base sur l'arrondi de l'UdM
    # multiplie par le nombre de moves.
    # 1.2.0 : rendement utile / dechets (is_waste_byproduct sur la categorie),
    # rendement par ligne sur stock.move, pourcentages en pleine precision
    # (arrondi deplace a l'affichage), etat `approx` pour la somme naive
    # poids + volume. `yield_state` gagne deux valeurs au total : les
    # consommateurs doivent traiter 'approx' ET 'over_100' (mrp_dashboard
    # prevenu).
    'version': '18.0.1.8.0',
    'category': 'Manufacturing',
    'summary': 'Rendement matiere reel des OF (sous-produits / composants)',
    'description': """
Rendement matiere sur les ordres de fabrication
===============================================

L'en-tete natif d'un OF affiche ``qty_produced / product_qty``. Lorsque le
produit fini est un produit-operation fictif (pivot) dont la quantite est
saisie a la main, ce ratio vaut 100 % par construction et ne mesure rien.

Ce module expose un rendement matiere reel, stocke, calcule comme :

    rendement = somme(sous-produits sortis) / somme(composants consommes)

Il distingue le rendement global (dechets inclus, c'est de la matiere sortie)
du rendement utile (hors dechets), la nature dechet etant portee par le
booleen `is_waste_byproduct` de la categorie de produit.

Les champs de `mrp.production` sont stockes afin de permettre le filtrage dans
la vue liste et l'exploitation en reporting externe (Metabase). Le rendement
par ligne sur `stock.move` est en revanche calcule a la volee.

Il devient la source de verite unique du rendement pour le dashboard
`mrp_dashboard` et le rapport `mrp_yield_report_of_plus_cost`.
""",
    'author': 'SOPROMER',
    'website': 'https://github.com/Lalaina710/mrp_material_yield',
    'license': 'LGPL-3',
    # `product` est deja une dependance transitive de `mrp`, mais on la declare
    # explicitement : le module herite `product.category` et etend
    # `product.product_category_form_view`.
    'depends': ['mrp', 'product'],
    'data': [
        'data/uom_category_data.xml',
        'views/mrp_production_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
