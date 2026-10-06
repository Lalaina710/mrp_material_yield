# -*- coding: utf-8 -*-
"""Coche `yield_mass_equivalent` sur Poids et Volume, a l'upgrade.

POURQUOI CE SCRIPT EXISTE
-------------------------
`data/uom_category_data.xml` porte `noupdate="1"` et cible des xmlid qui
appartiennent au module `uom`, pas au notre. En mode UPDATE (`-u`), ces lignes
ne sont donc jamais appliquees -- `odoo/tools/convert.py:341-350` :

    # in update mode, the record won't be updated if the data node explicitly
    # opt-out using @noupdate="1".
    if self.noupdate and self.mode != 'init':
        if record := env['ir.model.data']._load_xmlid(xid):
            ...
            return None

Les xmlid existant deja, le noeud est purement et simplement saute. Sur une
installation neuve (`mode == 'init'`) le fichier de donnees fait son travail ;
sur une base existante, rien n'est ecrit et le flag reste NULL.

Le `noupdate="1"` est conserve : il est correct pour l'installation neuve, et
il protege le choix de l'administrateur contre les upgrades suivants. Ce
script est le complement standard « nouvelle donnee sur base existante ».

NULL N'EST PAS False
--------------------
On ne coche QUE les categories dont la valeur est NULL, c'est-a-dire jamais
renseignee. Un administrateur qui a deliberement DECOCHE la case a `False` en
base, et l'upgrade ne doit pas revenir sur sa decision.

Via l'ORM, un Boolean rend `False` dans les deux cas : la distinction n'est
possible qu'en SQL (`IS NULL`). On lit donc en SQL, mais on ECRIT par l'ORM --
un `UPDATE` direct ne declencherait pas le recalcul des champs stockes du
rendement, qui dependent de
`move_*.product_uom.category_id.yield_mass_equivalent`, et les OF resteraient
figes sur `uom_mixed` sans le moindre signal.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

TARGET_XMLIDS = (
    "uom.product_uom_categ_kgm",   # Poids
    "uom.product_uom_categ_vol",   # Volume : erreur bornee par la densite
)


def migrate(cr, version):
    if not version:
        # Installation neuve : `data/uom_category_data.xml` est applique en
        # mode init, il n'y a rien a rattraper.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    categories = env["uom.category"]
    missing = []
    for xmlid in TARGET_XMLIDS:
        category = env.ref(xmlid, raise_if_not_found=False)
        if category:
            categories |= category
        else:
            missing.append(xmlid)
    if missing:
        _logger.warning(
            "mrp_material_yield: categories d'UdM introuvables (%s). "
            "Le module `uom` est-il installe ?", ", ".join(missing),
        )
    if not categories:
        return

    # Seul le SQL distingue NULL (jamais renseigne) de False (decoche a la
    # main). L'ORM rendrait False dans les deux cas.
    cr.execute(
        "SELECT id FROM uom_category "
        "WHERE id IN %s AND yield_mass_equivalent IS NULL",
        (tuple(categories.ids),),
    )
    todo_ids = [row[0] for row in cr.fetchall()]

    already_set = categories.ids
    if not todo_ids:
        _logger.info(
            "mrp_material_yield: aucune categorie a cocher, les %s ciblees "
            "portent deja une valeur explicite (choix conserve).",
            len(already_set),
        )
        return

    todo = env["uom.category"].browse(todo_ids)
    names = todo.mapped("name")
    # Ecriture par l'ORM : propage l'invalidation vers `material_yield` et
    # consorts. C'est tout l'interet de ne pas faire un UPDATE ici.
    todo.write({"yield_mass_equivalent": True})
    _logger.info(
        "mrp_material_yield: %s categorie(s) d'UdM cochee(s) assimilables au "
        "poids : %s. %s categorie(s) laissee(s) telle(s) quelle(s) "
        "(valeur deja explicite).",
        len(todo), ", ".join(names), len(already_set) - len(todo_ids),
    )
