# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMaterialYield(TransactionCase):
    """Rendement matiere : garde-fous, invariants et coherence des champs.

    Les OF sont montes a la main (moves ecrits directement) plutot que via un
    flux MRP complet : on teste le calcul du rendement, pas la mecanique de
    fabrication d'Odoo. Les quantites de reference viennent des OF reels
    US/MO/01877 et US/MO/01879 du 45.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uom_kg = cls.env.ref("uom.product_uom_kgm")
        cls.uom_litre = cls.env.ref("uom.product_uom_litre")
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")

        # Les categories assimilables au poids ne sont plus resolues par
        # xmlid mais lues depuis `uom.category.yield_mass_equivalent`. Les
        # tests posent donc explicitement l'etat qu'ils testent, au lieu de
        # dependre des donnees du module : un test qui s'appuierait sur
        # `data/uom_category_data.xml` passerait au vert pour une raison
        # exterieure a ce qu'il verifie.
        cls.categ_weight = cls.uom_kg.category_id
        cls.categ_volume = cls.uom_litre.category_id
        cls.categ_unit = cls.uom_unit.category_id
        # Ecriture conditionnelle : ces categories sont partagees par toute la
        # base. Une ecriture inutile declencherait le recalcul du rendement sur
        # tous les OF existants, via
        # `move_*.product_uom.category_id.yield_mass_equivalent`.
        for category, expected in (
            (cls.categ_weight, True),
            (cls.categ_volume, True),
            (cls.categ_unit, False),
        ):
            if category.yield_mass_equivalent != expected:
                category.yield_mass_equivalent = expected

        # Arrondi du kg : plusieurs tests utilisent des quantites a 3
        # decimales (AU_LEV01 = 0,038). `stock.move._set_quantity` refuse
        # toute quantite qui n'est pas un multiple de l'arrondi de l'UdM.
        #
        # Or l'arrondi n'est PAS garanti : le core ne declare aucun `rounding`
        # dans `uom/data/uom_data.xml`, donc le defaut du champ s'applique --
        # 0,01 (`uom_uom.py:66-67`). Sur la base SOPROMER le kg a ete passe a
        # 0,001 par le client, ce qui fait passer ces tests ; sur une base
        # vanilla ils echoueraient. On ne depend donc pas de l'ambiant.
        #
        # `rounding` n'est pas dans la liste des champs gardes par
        # `stock/models/product.py:1128` ('factor', 'factor_inv',
        # 'category_id') : cette ecriture ne peut pas lever.
        if cls.uom_kg.rounding > 0.001:
            cls.uom_kg.rounding = 0.001

        # Categorie parasite : reproduit l'artefact d'import Sage (une unite
        # nommee « kg », facteur 1, rangee hors de la categorie Poids). Non
        # assimilable par defaut, comme en production. Arrondi a 0,001 pour la
        # meme raison que ci-dessus.
        cls.categ_junk = cls.env["uom.category"].create({
            "name": "Unsorted/Imported Units (test)",
            "yield_mass_equivalent": False,
        })
        cls.uom_junk_kg = cls.env["uom.uom"].create({
            "name": "kg",
            "category_id": cls.categ_junk.id,
            "factor": 1.0,
            "uom_type": "reference",
            "rounding": 0.001,
        })

        cls.categ_waste = cls.env["product.category"].create({
            "name": "Dechet (test)",
            "is_waste_byproduct": True,
        })
        cls.categ_useful = cls.env["product.category"].create({
            "name": "Derive vendable (test)",
            "is_waste_byproduct": False,
        })

        cls.location_src = cls.env.ref("stock.stock_location_stock")
        # `stock.stock_location_production` N'EXISTE PAS comme external ID.
        # Contrairement a `stock_location_stock` (declare dans
        # stock/data/stock_data.xml:83), l'emplacement de production est cree
        # par societe, par du code, sans xmlid :
        # `res_company._create_production_location()` (res_company.py:80-89).
        # On le cherche donc par usage, comme le fait le core lui-meme.
        cls.location_prod = cls.env["stock.location"].search([
            ("usage", "=", "production"),
            ("company_id", "in", [False, cls.env.company.id]),
        ], limit=1)
        # Echouer ici avec un message clair plutot que plus loin sur un
        # `location_id` vide, qui donnerait une erreur sans rapport.
        assert cls.location_prod, (
            "Aucun emplacement de type 'production' pour la societe %s. "
            "Il est normalement cree a l'installation de `stock` ; verifier "
            "res_company.create_missing_production_location()."
            % cls.env.company.display_name
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _product(cls, name, uom=None, categ=None):
        return cls.env["product.product"].create({
            "name": name,
            "is_storable": True,
            "uom_id": (uom or cls.uom_kg).id,
            "uom_po_id": (uom or cls.uom_kg).id,
            "categ_id": (categ or cls.categ_useful).id,
        })

    def _move_vals(self, product, qty, picked=True, state="done"):
        return {
            "name": product.name,
            "product_id": product.id,
            "product_uom": product.uom_id.id,
            "product_uom_qty": qty,
            "quantity": qty,
            "picked": picked,
            "state": state,
            "location_id": self.location_src.id,
            "location_dest_id": self.location_prod.id,
        }

    def _make_mo(self, pivot_qty=100.0, components=(), byproducts=(), uom=None):
        """Cree un OF avec son produit-pivot, ses composants, ses sous-produits.

        `components` / `byproducts` : iterables de (product, qty) ou
        (product, qty, kwargs_move).
        """
        pivot = self._product("PIVOT", uom=uom or self.uom_kg)
        mo = self.env["mrp.production"].create({
            "product_id": pivot.id,
            "product_uom_id": pivot.uom_id.id,
            "product_qty": pivot_qty,
        })
        raw_cmds, fin_cmds = [], []
        for entry in components:
            product, qty = entry[0], entry[1]
            extra = entry[2] if len(entry) > 2 else {}
            raw_cmds.append(Command.create({**self._move_vals(product, qty), **extra}))
        for entry in byproducts:
            product, qty = entry[0], entry[1]
            extra = entry[2] if len(entry) > 2 else {}
            vals = self._move_vals(product, qty)
            # Un sous-produit va de la production vers le stock.
            vals["location_id"] = self.location_prod.id
            vals["location_dest_id"] = self.location_src.id
            fin_cmds.append(Command.create({**vals, **extra}))
        writes = {}
        if raw_cmds:
            writes["move_raw_ids"] = raw_cmds
        if fin_cmds:
            writes["move_finished_ids"] = fin_cmds
        if writes:
            mo.write(writes)
        mo.invalidate_recordset()
        return mo

    def _assert_invariant(self, mo):
        """input == utile + dechet + perte, et output == utile + dechet.

        Invariant sur lequel `mrp_dashboard` s'appuie pour recomposer ses
        pourcentages a partir des sommes de quantites.
        """
        self.assertAlmostEqual(
            mo.yield_output_qty,
            mo.yield_useful_qty + mo.yield_waste_qty, places=6,
            msg="output != utile + dechet sur %s" % mo.name,
        )
        self.assertAlmostEqual(
            mo.yield_input_qty,
            mo.yield_useful_qty + mo.yield_waste_qty + mo.yield_loss_qty,
            places=6,
            msg="input != utile + dechet + perte sur %s" % mo.name,
        )

    # ------------------------------------------------------------------
    # Controle statique : ensemble contre ensemble
    # ------------------------------------------------------------------
    def test_00_declared_fields_match_constants(self):
        """Tout champ calcule par `_compute_material_yield` est dans les constantes.

        C'est le garde-fou qui ferme completement la boucle. Le cas residuel
        qu'il attrape -- un champ ajoute au dict du chemin nominal mais absent
        de `_YIELD_QTY_FIELDS` / `_YIELD_PCT_FIELDS` -- ne casserait QUE le
        chemin `_set_yield_undefined`, emprunte par tout OF annule (132 OF sur
        1824 sur le 45). Aucun test nominal ne le verrait, et comme le backfill
        recalcule toute la table a l'installation, c'est l'upgrade lui-meme qui
        echouerait.

        Ils sont 11 : les 9 numeriques, plus `yield_uom_id` et `yield_state`
        qui ne peuvent pas passer par `dict.fromkeys(..., 0.0)` -- l'un est un
        Many2one, l'autre une Selection.
        """
        Production = self.env["mrp.production"]
        declared = {
            name for name, field in Production._fields.items()
            if field.compute == "_compute_material_yield"
        }
        covered = set(Production._YIELD_QTY_FIELDS) | set(
            Production._YIELD_PCT_FIELDS
        ) | {"yield_uom_id", "yield_state"}

        self.assertEqual(
            declared, covered,
            "Desynchronisation entre les champs calcules par "
            "_compute_material_yield et les constantes de classe.\n"
            "Declares mais non couverts : %s\n"
            "Couverts mais non declares : %s"
            % (sorted(declared - covered), sorted(covered - declared)),
        )
        self.assertEqual(len(declared), 11, "11 champs attendus, %s trouves" % len(declared))
        # Les deux non-numeriques ne doivent surtout pas etre dans les tuples
        # numeriques : `dict.fromkeys(..., 0.0)` les mettrait a 0.
        numeric = set(Production._YIELD_QTY_FIELDS) | set(Production._YIELD_PCT_FIELDS)
        self.assertNotIn("yield_uom_id", numeric)
        self.assertNotIn("yield_state", numeric)

    # ------------------------------------------------------------------
    # Cas nominal
    # ------------------------------------------------------------------
    def test_01_nominal_us_mo_01877(self):
        """OF nominal : 1 composant, 7 sous-produits dont 1 dechet.

        Chiffres de US/MO/01877 (FILETAGE SOLE), valides en runtime sur le 45.
        """
        comp = self._product("TRSOL03")
        waste = self._product("DECHETS", categ=self.categ_waste)
        mo = self._make_mo(
            components=[(comp, 86.560)],
            byproducts=[
                (waste, 3.100),
                (self._product("DRDPV02"), 10.520),
                (self._product("DRDPV07"), 0.900),
                (self._product("DRDPV04"), 28.220),
                (self._product("DROEU01"), 0.100),
                (self._product("FLCHA01"), 9.300),
                (self._product("FLSOL09"), 32.200),
            ],
        )
        self.assertEqual(mo.yield_state, "ok")
        self.assertAlmostEqual(mo.yield_input_qty, 86.560, places=3)
        self.assertAlmostEqual(mo.yield_output_qty, 84.340, places=3)
        self.assertAlmostEqual(mo.yield_waste_qty, 3.100, places=3)
        self.assertAlmostEqual(mo.yield_useful_qty, 81.240, places=3)
        self.assertAlmostEqual(mo.yield_loss_qty, 2.220, places=3)
        # Pleine precision : le champ n'est PAS pre-arrondi a 2 decimales.
        self.assertAlmostEqual(mo.material_yield, 97.43530499075786, places=9)
        self.assertAlmostEqual(mo.yield_useful_pct, 93.8539741219963, places=9)
        self.assertAlmostEqual(mo.yield_waste_pct, 3.581330868761553, places=9)
        self.assertAlmostEqual(mo.yield_loss_pct, 2.5646950092421426, places=9)
        self.assertEqual(mo.yield_uom_id, self.uom_kg)
        self.assertFalse(mo.yield_warning)
        self._assert_invariant(mo)

    def test_02_percentages_do_not_recompose_once_rounded(self):
        """Verrou sur le piege documente : 93,85 + 3,58 = 97,43 != 97,44.

        Chaque pourcentage est juste a sa precision, leur somme ne l'est pas.
        Ce test fige le comportement pour qu'on ne « corrige » pas un jour le
        rendement global en le recalculant a partir des deux autres.

        Ironie a conserver : ce test-ci est tombe dans le piege qu'il
        documente. Sa premiere version comparait
        `round(utile, 2) + round(dechet, 2)` a `97.43` avec `assertEqual` --
        or `93.85 + 3.58` vaut `97.42999999999999` en binaire, aucun des deux
        n'ayant de representation exacte. On arrondit donc la SOMME, ce qui
        reproduit ce que l'utilisateur voit : le client web additionne puis
        formate.

        A distinguer des autres assertions du fichier, de la forme
        `round(x, 2) == litteral` : celles-la sont exactes par construction,
        les deux cotes passant par le meme « double le plus proche de cette
        decimale ». C'est l'ARITHMETIQUE sur des valeurs deja arrondies qui
        est dangereuse, pas l'arrondi lui-meme.
        """
        comp = self._product("TRSOL03")
        waste = self._product("DECHETS", categ=self.categ_waste)
        mo = self._make_mo(
            components=[(comp, 86.560)],
            byproducts=[(waste, 3.100), (self._product("FILET"), 81.240)],
        )
        self.assertEqual(round(mo.material_yield, 2), 97.44)
        self.assertEqual(round(mo.yield_useful_pct, 2), 93.85)
        self.assertEqual(round(mo.yield_waste_pct, 2), 3.58)
        somme_affichee = round(
            round(mo.yield_useful_pct, 2) + round(mo.yield_waste_pct, 2), 2
        )
        self.assertEqual(
            somme_affichee, 97.43,
            "La somme des parts arrondies vaut 97,43 et non 97,44 : c'est "
            "attendu, et c'est precisement ce que ce test verrouille.",
        )
        self.assertNotEqual(
            somme_affichee, round(mo.material_yield, 2),
            "Si la somme des parts egalait le global, le piege documente "
            "aurait disparu et l'avertissement des help= serait a retirer.",
        )
        # ... alors que la somme des valeurs BRUTES redonne bien le global.
        self.assertAlmostEqual(
            mo.yield_useful_pct + mo.yield_waste_pct, mo.material_yield, places=9,
        )

    def test_03_line_yield_sums_to_header(self):
        """Somme des `yield_pct` des sous-produits == `material_yield`.

        Critere d'acceptation n1 : la ligne de total de l'onglet Sous-produits
        doit egaler l'en-tete. Le client web additionne les valeurs BRUTES puis
        formate ; on reproduit exactement cela.
        """
        comp = self._product("TRSOL03")
        waste = self._product("DECHETS", categ=self.categ_waste)
        mo = self._make_mo(
            components=[(comp, 86.560)],
            byproducts=[
                (waste, 3.100),
                (self._product("DRDPV02"), 10.520),
                (self._product("DRDPV07"), 0.900),
                (self._product("DRDPV04"), 28.220),
                (self._product("DROEU01"), 0.100),
                (self._product("FLCHA01"), 9.300),
                (self._product("FLSOL09"), 32.200),
            ],
        )
        total = sum(mo.move_byproduct_ids.mapped("yield_pct"))
        self.assertAlmostEqual(total, mo.material_yield, places=9)
        self.assertEqual(round(total, 2), round(mo.material_yield, 2))
        self.assertEqual(round(total, 2), 97.44)

    # ------------------------------------------------------------------
    # Garde-fous
    # ------------------------------------------------------------------
    def test_04_no_byproduct(self):
        """Aucun sous-produit -> `no_byproduct`, surtout pas 0 %.

        Cas reel US/MO/00090 (pivot RCTR, 1 composant, 0 sous-produit).
        """
        mo = self._make_mo(components=[(self._product("TRSOL03"), 50.0)])
        self.assertEqual(mo.yield_state, "no_byproduct")
        self.assertEqual(mo.material_yield, 0.0)
        self.assertEqual(mo.yield_input_qty, 0.0)
        self.assertFalse(mo.yield_uom_id)

    def test_05_no_input(self):
        """Aucun composant -> `no_input`."""
        mo = self._make_mo(byproducts=[(self._product("FILET"), 10.0)])
        self.assertEqual(mo.yield_state, "no_input")
        self.assertEqual(mo.material_yield, 0.0)

    def test_06_cancelled_mo_takes_the_guard_path(self):
        """OF annule : tous ses moves sont `cancel` -> `no_input`.

        C'est le chemin le plus frequent de `_set_yield_undefined` (132 OF sur
        1824 sur le 45). Il doit assigner les 11 champs sans lever.
        """
        comp = self._product("TRSOL03")
        mo = self._make_mo(
            components=[(comp, 86.560, {"state": "cancel", "picked": False})],
            byproducts=[(self._product("FILET"), 84.340,
                         {"state": "cancel", "picked": False})],
        )
        self.assertEqual(mo.yield_state, "no_input")
        for fname in (
            self.env["mrp.production"]._YIELD_QTY_FIELDS
            + self.env["mrp.production"]._YIELD_PCT_FIELDS
        ):
            self.assertEqual(mo[fname], 0.0, "%s non neutralise" % fname)
        self.assertFalse(mo.yield_uom_id)

    def test_07_uom_mixed_blocks_and_names_all_categories(self):
        """Poids + Unites -> `uom_mixed`, aucun chiffre, message explicite."""
        comp_kg = self._product("TRSOL03", uom=self.uom_kg)
        comp_unit = self._product("BALAX", uom=self.uom_unit)
        mo = self._make_mo(
            components=[(comp_kg, 50.0), (comp_unit, 3.0)],
            byproducts=[(self._product("FILET"), 40.0)],
        )
        self.assertEqual(mo.yield_state, "uom_mixed")
        self.assertEqual(mo.material_yield, 0.0)
        self.assertTrue(mo.yield_warning)
        # Le message doit nommer les categories presentes, pas seulement une.
        self.assertIn("BALAX", mo.yield_warning)
        self.assertIn("Catégories présentes", mo.yield_warning)

    def test_08_approx_weight_plus_volume(self):
        """Poids + Volume -> `approx`, calcul fait, avertissement nomme.

        Somme naive : les litres sont additionnes aux kilogrammes. Chiffres de
        US/MO/01879 hors le composant en categorie fourre-tout.

        Les deux categories portent `yield_mass_equivalent` (pose dans
        `setUpClass`) : c'est le flag qui autorise la somme, plus une
        resolution d'xmlid en dur.
        """
        mo = self._make_mo(
            components=[
                (self._product("TVBAN0", uom=self.uom_kg), 33.140),
                (self._product("EAU", uom=self.uom_litre), 6.270),
                (self._product("AU_FEC03", uom=self.uom_kg), 1.700),
                (self._product("AU_HUI02", uom=self.uom_litre), 1.700),
                (self._product("EN_SEL03", uom=self.uom_kg), 0.340),
                # Composant a 3 decimales : ne doit pas etre perdu.
                (self._product("AU_LEV01", uom=self.uom_kg), 0.038),
            ],
            byproducts=[
                (self._product("FLBOU01"), 22.710),
                (self._product("TRBAN01"), 8.180),
                (self._product("DECHETS", categ=self.categ_waste), 5.700),
                (self._product("DRDPV01"), 4.300),
            ],
        )
        self.assertEqual(mo.yield_state, "approx")
        self.assertAlmostEqual(mo.yield_input_qty, 43.188, places=3)
        self.assertAlmostEqual(mo.yield_output_qty, 40.890, places=3)
        self.assertAlmostEqual(mo.material_yield, 94.67907752153376, places=9)
        self.assertEqual(round(mo.material_yield, 2), 94.68)
        self.assertEqual(round(mo.yield_useful_pct, 2), 81.48)
        self.assertEqual(round(mo.yield_waste_pct, 2), 13.20)
        # UdM d'affichage = categorie majoritaire (4 lignes kg contre 2 L).
        self.assertEqual(mo.yield_uom_id, self.uom_kg)
        self.assertTrue(mo.yield_warning)
        self.assertIn("EAU", mo.yield_warning)
        self.assertIn("AU_HUI02", mo.yield_warning)
        self._assert_invariant(mo)

    def _make_mo_01879(self):
        """Reconstitue US/MO/01879 avec EN_SEL03 en categorie parasite.

        6 composants sur TROIS categories d'UdM : Poids (4 lignes), Volume
        (2 lignes) et la categorie fourre-tout d'import (EN_SEL03, dont l'UdM
        s'appelle « kg » mais n'est pas rangee dans Poids).
        """
        return self._make_mo(
            components=[
                (self._product("TVBAN0", uom=self.uom_kg), 33.140),
                (self._product("EAU", uom=self.uom_litre), 6.270),
                (self._product("AU_FEC03", uom=self.uom_kg), 1.700),
                (self._product("AU_HUI02", uom=self.uom_litre), 1.700),
                (self._product("EN_SEL03", uom=self.uom_junk_kg), 0.340),
                (self._product("AU_LEV01", uom=self.uom_kg), 0.038),
            ],
            byproducts=[
                (self._product("FLBOU01"), 22.710),
                (self._product("TRBAN01"), 8.180),
                (self._product("DECHETS", categ=self.categ_waste), 5.700),
                (self._product("DRDPV01"), 4.300),
            ],
        )

    def test_08a_three_categories_one_unflagged_blocks(self):
        """Etat REEL de US/MO/01879 : la 3e categorie n'est pas assimilable.

        C'est le cas de production tant que la categorie parasite issue de
        l'import Sage n'est pas cochee. La borne doit tenir : une seule
        categorie non declaree suffit a bloquer, meme si son UdM s'appelle
        « kg » et vaut effectivement des kilogrammes. Le module ne devine pas.
        """
        mo = self._make_mo_01879()
        self.assertEqual(mo.yield_state, "uom_mixed")
        self.assertEqual(mo.material_yield, 0.0)
        self.assertEqual(mo.yield_input_qty, 0.0)
        # Le message doit designer la categorie fautive, puis annoncer que le
        # resultat sera APPROXIMATIF apres correction -- pas exact.
        self.assertIn("EN_SEL03", mo.yield_warning)
        self.assertIn("APPROXIMATIF", mo.yield_warning)

    def test_08b_three_categories_all_flagged_computes(self):
        """Etat APRES configuration : les 3 categories sont assimilables.

        Cocher `yield_mass_equivalent` sur la categorie parasite suffit a
        debloquer l'OF, sans toucher a la moindre donnee de stock. C'est tout
        l'objet du flag : la correction de l'UdM elle-meme est refusee par
        `stock/models/product.py:1126-1155` des qu'un quant non nul existe.

        Verifie aussi que le `@api.depends` couvre le flag : l'OF est cree
        AVANT que la case ne soit cochee, donc le recalcul doit etre
        declenche par l'ecriture sur la categorie.
        """
        mo = self._make_mo_01879()
        self.assertEqual(mo.yield_state, "uom_mixed", "etat initial attendu")

        self.categ_junk.write({"yield_mass_equivalent": True})

        self.assertEqual(
            mo.yield_state, "approx",
            "Le flag doit declencher le recalcul via @api.depends "
            "(move_*.product_uom.category_id.yield_mass_equivalent).",
        )
        self.assertAlmostEqual(mo.yield_input_qty, 43.188, places=3)
        self.assertAlmostEqual(mo.yield_output_qty, 40.890, places=3)
        self.assertAlmostEqual(mo.material_yield, 94.67907752153376, places=9)
        self.assertEqual(round(mo.material_yield, 2), 94.68)
        self.assertEqual(round(mo.yield_useful_pct, 2), 81.48)
        self.assertEqual(round(mo.yield_waste_pct, 2), 13.20)
        self.assertEqual(round(mo.yield_loss_pct, 2), 5.32)
        # L'UdM d'affichage reste le Poids : 4 lignes kg contre 2 L et 1 junk.
        self.assertEqual(mo.yield_uom_id, self.uom_kg)
        self._assert_invariant(mo)

    def test_08c_flag_is_read_not_hardcoded(self):
        """Decocher Volume rebascule un OF Poids+Volume en `uom_mixed`.

        Preuve que la regle lit bien le flag et ne resout plus les categories
        par xmlid : le meme OF change d'etat selon la configuration seule.
        """
        mo = self._make_mo(
            components=[
                (self._product("TVBAN0", uom=self.uom_kg), 40.000),
                # 3,19 et non 3,188 : l'UdM « L » a un arrondi de 0,01 (defaut
                # du core, `uom_uom.py:66-67`), et `stock.move._set_quantity`
                # refuse toute quantite qui n'en est pas un multiple. Ce test
                # ne porte pas sur des valeurs metier, seulement sur la
                # bascule d'etat : la quantite doit juste etre valide.
                (self._product("EAU", uom=self.uom_litre), 3.190),
            ],
            byproducts=[(self._product("FLBOU01"), 40.890)],
        )
        self.assertEqual(mo.yield_state, "approx")

        self.categ_volume.write({"yield_mass_equivalent": False})
        self.assertEqual(
            mo.yield_state, "uom_mixed",
            "Decocher la categorie doit bloquer le calcul.",
        )

        self.categ_volume.write({"yield_mass_equivalent": True})
        self.assertEqual(
            mo.yield_state, "approx", "Le flag doit etre reversible.",
        )

    def test_09_three_decimal_component_not_lost(self):
        """Un composant de 0,038 kg doit peser dans le denominateur."""
        big = self._product("TVBAN0")
        tiny = self._product("AU_LEV01")
        with_tiny = self._make_mo(
            components=[(big, 10.000), (tiny, 0.038)],
            byproducts=[(self._product("FILET"), 9.000)],
        )
        without_tiny = self._make_mo(
            components=[(self._product("TVBAN0b"), 10.000)],
            byproducts=[(self._product("FILETb"), 9.000)],
        )
        self.assertAlmostEqual(with_tiny.yield_input_qty, 10.038, places=3)
        self.assertNotAlmostEqual(
            with_tiny.material_yield, without_tiny.material_yield, places=4,
            msg="Le composant a 3 decimales n'a pas ete pris en compte.",
        )

    def test_10_over_100_flagged(self):
        """Sortir plus que l'on engage -> `over_100`, valeur conservee.

        Chiffres de US/MO/00441 : 12,000 engages, 19,720 sortis.
        """
        mo = self._make_mo(
            components=[(self._product("TVBAN0"), 12.000)],
            byproducts=[(self._product("TRBAN01"), 19.720)],
        )
        self.assertEqual(mo.yield_state, "over_100")
        # La valeur n'est PAS masquee : c'est le signal d'exploitation.
        self.assertAlmostEqual(mo.material_yield, 164.33333333333334, places=6)
        self.assertAlmostEqual(mo.yield_input_qty, 12.000, places=3)
        self.assertAlmostEqual(mo.yield_output_qty, 19.720, places=3)
        self.assertTrue(mo.yield_warning)
        self._assert_invariant(mo)

    def test_11_over_100_threshold_is_relative_not_absolute(self):
        """Le seuil vaut `rounding x nb_moves`, pas une constante.

        Un ecart sous le plancher de bruit reste `ok` ; au-dessus il bascule.
        Avec 2 moves a 0,001 le plancher vaut 0,002.
        """
        below = self._make_mo(
            components=[(self._product("A"), 100.000)],
            byproducts=[(self._product("B"), 100.001)],
        )
        self.assertEqual(
            below.yield_state, "ok",
            "Un ecart de 0,001 sur 2 moves est du bruit d'arrondi, pas une anomalie.",
        )
        above = self._make_mo(
            components=[(self._product("C"), 100.000)],
            byproducts=[(self._product("D"), 100.500)],
        )
        self.assertEqual(above.yield_state, "over_100")

    def test_12_unpicked_move_falls_back_to_demand(self):
        """Move non `picked` : on retombe sur la quantite planifiee."""
        mo = self._make_mo(
            components=[(self._product("TRSOL03"), 100.0,
                         {"picked": False, "state": "confirmed",
                          "quantity": 0.0, "product_uom_qty": 100.0})],
            byproducts=[(self._product("FILET"), 90.0,
                         {"picked": False, "state": "confirmed",
                          "quantity": 0.0, "product_uom_qty": 90.0})],
        )
        self.assertEqual(mo.yield_state, "ok")
        self.assertAlmostEqual(mo.yield_input_qty, 100.0, places=3)
        self.assertAlmostEqual(mo.yield_output_qty, 90.0, places=3)

    def test_13_waste_flag_drives_useful_split(self):
        """Sans categorie cochee, utile == global et dechets == 0."""
        mo = self._make_mo(
            components=[(self._product("TRSOL03"), 100.0)],
            byproducts=[
                (self._product("DECHETS", categ=self.categ_useful), 10.0),
                (self._product("FILET"), 80.0),
            ],
        )
        self.assertEqual(mo.yield_waste_qty, 0.0)
        self.assertAlmostEqual(mo.yield_useful_pct, mo.material_yield, places=9)
        self._assert_invariant(mo)
