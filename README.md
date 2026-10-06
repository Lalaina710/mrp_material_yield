# MRP Material Yield

Rendement matière réel sur les ordres de fabrication Odoo 18.
**Source de vérité unique du rendement** pour le formulaire OF, le dashboard
`mrp_dashboard` et le rapport PDF `mrp_yield_report_of_plus_cost`.

## Problème résolu

L'en-tête natif d'un OF affiche `qty_producing / product_qty` — « Quantité X / X kg
À produire ». Chez SOPROMER, le produit fini d'un OF de transformation est un
**produit-opération fictif** (pivot, `is_storable = False`) dont la quantité est
saisie à la main, par convention égale à la matière engagée. Les vrais produits
sortent en **sous-produits**.

Ce ratio vaut donc **100 % sur tout OF terminé, par construction**. Le même défaut
existait dans `mrp_dashboard` (`finished_qty = mo.qty_produced`), pendant que le
rapport PDF utilisait, lui, la bonne formule — deux rendements différents pour le
même OF.

```
rendement global = Σ sous-produits sortis / Σ composants consommés
rendement utile  = Σ sous-produits hors déchets / Σ composants consommés
```

Les **déchets comptent dans le rendement global** — c'est de la matière sortie, pas
de la perte — et sont exclus du rendement utile.

## Champs ajoutés

### `mrp.production` — tous stockés

Le stockage permet le filtrage en vue liste et le `read_group` direct depuis
`mrp_dashboard` / Metabase, sans réimplémenter le calcul.

| Champ | `digits` | Sens |
|---|---|---|
| `yield_input_qty` | Product Unit of Measure | Σ composants réellement consommés |
| `yield_output_qty` | Product Unit of Measure | Σ sous-produits (= utile + déchets) |
| `yield_useful_qty` | Product Unit of Measure | Σ sous-produits hors déchets |
| `yield_waste_qty` | Product Unit of Measure | Σ sous-produits marqués déchets |
| `yield_loss_qty` | Product Unit of Measure | `input − output` |
| `material_yield` | **aucun** | `output / input × 100` |
| `yield_useful_pct` | **aucun** | `useful / input × 100` |
| `yield_waste_pct` | **aucun** | `waste / input × 100` |
| `yield_loss_pct` | **aucun** | `loss / input × 100` |
| `yield_uom_id` | — | UdM de référence des quantités |
| `yield_state` | — | `ok` / `approx` / `over_100` / `no_byproduct` / `uom_mixed` / `no_input` |
| `yield_warning` | — | non stocké ; message pour `approx`, `over_100` et `uom_mixed` |

### ⚠ Les pourcentages ne se recomposent pas une fois arrondis

Sur `US/MO/01877` : `93,85` (utile) + `3,58` (déchets) = **97,43**, alors que le
rendement global affiche **97,44**.

Ce n'est pas un bug. Chaque pourcentage est juste à sa précision ; la somme de
deux valeurs arrondies ne l'est pas. Les valeurs **brutes**, elles, se
recomposent exactement — c'est sur elles que porte l'invariant ci-dessous.

Quiconque recalcule l'un des trois à partir des deux autres obtiendra un écart
d'un centième et croira à une incohérence. C'est ce piège qui a produit un
« 93,86 » erroné en cours de projet, lequel a ensuite contaminé une fixture de
test. Un test (`test_02_percentages_do_not_recompose_once_rounded`) fige
explicitement ce comportement pour éviter qu'on le « corrige ».

**Invariant garanti** : `yield_input_qty = yield_useful_qty + yield_waste_qty +
yield_loss_qty`, et `yield_output_qty = yield_useful_qty + yield_waste_qty`.
`output` est calculé comme `useful + waste` et non par une troisième sommation :
additionner les mêmes flottants dans un ordre différent peut diverger d'un ulp.
`mrp_dashboard` s'appuie sur cet invariant pour recomposer ses pourcentages.

### `stock.move` — non stocké

| Champ | Sens |
|---|---|
| `yield_pct` | `quantité du move / yield_input_qty de l'OF parent × 100` |
| `yield_parent_state` | related `production_id.yield_state`, technique |

Le dénominateur est **lu** sur l'OF parent, jamais recalculé : il n'existe qu'une
seule sommation des composants dans le module.

### `product.category`

| Champ | Sens |
|---|---|
| `is_waste_byproduct` | marque la catégorie comme déchet de transformation |

### `uom.category`

| Champ | Sens |
|---|---|
| `yield_mass_equivalent` | catégorie d'UdM assimilable au poids pour le bilan matière |

## Pourquoi les pourcentages n'ont pas de `digits`

C'est le point le plus contre-intuitif du module, et il est **vérifié, pas supposé**.

`Float.convert_to_cache` (`odoo/fields.py:1655-1659`) :

```python
def convert_to_cache(self, value, record, validate=True):
    # apply rounding here, otherwise value in cache may be wrong!
    value = float(value or 0.0)
    digits = self.get_digits(record.env)
    return float_round(value, precision_digits=digits[1]) if digits else value
```

Un `digits=(16, 2)` **n'est pas un paramètre de formatage** : il arrondit la valeur
au niveau de l'ORM, que le champ soit stocké ou non.

Or l'agrégat `sum` d'une vue liste additionne les valeurs **brutes** des
enregistrements puis formate le total (`list_renderer.js:628`, puis `631-641`).

Conséquence mesurée sur les 7 sous-produits de `US/MO/01877` :

| | Total onglet Sous-produits | En-tête |
|---|---|---|
| avec `digits=(16,2)` sur le champ | **97,43 %** | 97,44 % |
| sans `digits` (retenu) | **97,44 %** | 97,44 % |

Sur `US/MO/01879` (4 lignes) les deux variantes donneraient 94,68 % — l'écart
est **intermittent**, ce qui le rend d'autant plus dangereux.

⚠ `US/MO/01879` est aujourd'hui en `uom_mixed` sur la base réelle : un de ses
composants (`EN_SEL03`) est rangé dans une catégorie d'UdM parasite issue de
l'import Sage. Il **passerait** en `approx` à 94,68 % une fois cette donnée
régularisée. Ce chiffre est donc une valeur attendue, pas une mesure.

Le module garde donc la pleine précision dans le champ
(`material_yield = 97.43530499075786`) et arrondit uniquement à l'affichage, via
`digits="[16, 2]"` sur chaque nœud de vue (`float_field.js:126-131`).

## Règles de calcul

### Quantité retenue par move

- `state == 'cancel'` → move ignoré.
- `picked == True` → `move.quantity`. En Odoo 18, `stock.move.picked` vaut
  systématiquement `True` dès `state == 'done'` (`stock_move.py:261-267`), donc sur
  un OF terminé ce cas couvre tout.
- `picked == False` → `move.product_uom_qty` (demande planifiée). Le move n'a rien
  consommé, mais sur un OF en cours c'est la seule estimation disponible : la
  valeur est **provisoire**.

### Détection du déchet

Par le booléen `product.category.is_waste_byproduct`, porté par ce module.

**Pas** par le nom de catégorie (`'dechet' in categ.name`, comme le fait
`report_mrp_yield_cost.py:111-113`) : dépend de l'orthographe, des accents, de la
langue et du renommage.

**Pas non plus** par `exclude_from_mrp_byproduct_costshare` de
`mrp_byproduct_costshare_sale_price`. Deux raisons indépendantes : sa sémantique est
différente — son propre texte d'aide dit *« Do NOT exclude waste/scrap from sorting
— they must participate with their residual value »*, donc un déchet doit être
inclus dans le cost share tout en étant un déchet ; et le réutiliser coupleraient le
rendement matière au module de valorisation.

### Garde-fous

| `yield_state` | Déclencheur | Comportement vue |
|---|---|---|
| `no_input` | aucun composant, ou Σ composants ≈ 0 | rien affiché |
| `no_byproduct` | aucun sous-produit (cas réel `US/MO/00090`) | rien affiché — surtout pas « 0 % » |
| `uom_mixed` | ≥ 2 catégories, dont une sans `yield_mass_equivalent` | avertissement, **aucun pourcentage** |
| `approx` | ≥ 2 catégories, **toutes** avec `yield_mass_equivalent` | valeurs affichées **+ avertissement nommant les produits** |
| `over_100` | `output > input` au-delà du bruit d'arrondi | valeurs affichées **en rouge**, exclu des agrégats |
| `ok` | une seule catégorie | rendement + déchets + perte |

### L'état `over_100`

Sortir plus de matière qu'on en engage est physiquement impossible : c'est une
anomalie de saisie. Cas réels mesurés : `US/MO/00441` 164,33 % (12,000 → 19,720),
`US/MO/00438` 111,99 %, `US/MO/00450` 100,11 %. Ces OF passaient auparavant en
`ok` et gonflaient la moyenne du dashboard.

Le seuil n'est pas un epsilon arbitraire :

```python
noise_floor = uom.rounding * (len(raw_moves) + len(byproduct_moves))
float_compare(output_qty, input_qty, precision_rounding=noise_floor) > 0
```

Chaque move apporte au plus un arrondi d'UdM, donc le bruit cumulé d'une somme
de N moves est borné par `rounding × N` — soit 0,008 kg pour 8 mouvements à
0,001, environ 0,01 % sur un OF de 86 kg. À ce compte 100,11 % dépasse largement
le plancher de bruit et reste une vraie anomalie.

Basé sur l'arrondi de l'UdM plutôt que sur une constante en dur : reste juste si
des produits arrivent un jour avec une précision différente.

La valeur **n'est jamais masquée** — c'est le seul écran où l'exploitation peut
repérer l'erreur de saisie. `over_100` prime sur `approx`, mais l'avertissement
mentionne les deux quand les deux s'appliquent (des litres comptés comme des kg
peuvent être la cause même du dépassement).

Même catégorie mais UdM différentes → conversion vers l'UdM de référence via
`uom.uom._compute_quantity(..., round=False)`. `round=False` est obligatoire : le
défaut est `round=True, rounding_method='UP'` (`uom_uom.py:211`), qui arrondirait
chaque move à la hausse.

### L'état `approx`

Somme naïve des litres et des kilogrammes. Densité 1 pour l'eau (erreur nulle),
~0,92 pour l'huile (~0,14 kg sur 1,7 L). SOPROMER additionne déjà kg et litres
dans sa saisie — le `product_qty` du pivot de `US/MO/01879` vaut exactement
43,188, soit la somme naïve.

Les catégories autorisées ne sont **pas codées en dur** : elles portent le
booléen `uom.category.yield_mass_equivalent`. Le module coche Poids et Volume à
l'installation ; toute autre catégorie relève d'une décision explicite.

⚠ **À réserver aux grandeurs dont l'écart au poids est borné.** Cocher une
catégorie d'Unités produirait une erreur **non bornée** — 3 sachets peuvent peser
75 g comme 15 kg — et rendrait le rendement arbitraire. Le `help=` du champ le
dit à l'écran.

La conversion intra-catégorie reste exacte : chaque move est ramené à l'UdM de
référence de **sa** catégorie (kg pour Poids, L pour Volume —
`uom_data.xml:88-94` confirme que la référence Volume est le litre, pas le m³),
puis les totaux sont additionnés. Seul le franchissement Poids↔Volume est naïf.

Les OF `approx` **comptent dans les agrégats** : 11 sur 1824, erreur bornée, les
exclure ferait disparaître des OF réels des statistiques.

## Pourquoi pas `move_byproduct_ids` dans le `@api.depends`

`move_byproduct_ids` (`mrp/models/mrp_production.py:188`) n'a ni `store=True`, ni
`search=`, ni `inverse_name`. Chaîne pour un champ **stocké** qui en dépendrait :

1. `fields.py:896-897` — `_description_searchable` → **False**.
2. `field_inverses[move_byproduct_ids]` vide.
3. `models.py:7304-7309` — `_modified_triggers` exécute
   `mrp.production.search([('move_byproduct_ids', 'in', move_ids)])`.
4. `expression.py:1166-1174` — champ non stocké sans `search` : log ERROR **puis
   « Ignore it: generate a dummy leaf »**, `domain = []` → matche toute la table.

Chaque écriture sur un `stock.move` de sous-produit aurait recalculé le rendement
sur **tous les OF de la base**. Le module dépend donc de `move_finished_ids`
(stocké, `inverse_name='production_id'`) et refait le filtre sous-produit à
l'identique du core (`_compute_move_byproduct_ids`, `mrp_production.py:695`
sur le core déployé du 45 — le numéro dérive entre point releases, l'ancre
fiable est le nom de la méthode), **plus** l'exclusion des moves annulés que le
core ne fait pas.

La chaîne `move_finished_ids.product_id.categ_id.is_waste_byproduct` est en revanche
valide : `product.product.categ_id` est un champ délégué (`_inherits`) dont la cible
`product.template.categ_id` est stockée, donc `setup_related` (`fields.py:626-628`)
lui attribue `search = _search_related`. Précédent core :
`stock/models/stock_orderpoint.py:53` déclare un related **stocké** sur ce chemin.

## Vue

- **Formulaire OF** : bloc Rendement / Déchets / Perte sous la ligne Quantité.
- **Onglet Sous-produits** : colonne `Rendement %` avec `sum="Rendement total"`.
  Tous les sous-produits partagent le même dénominateur, donc la somme des lignes
  **est** le rendement global : le total doit égaler l'en-tête. Divergence à
  l'écran = signal de bug, contrôle gratuit.
- **Liste OF** : `Rendement %` (`optional="show"`), `Rendement utile %`, `Déchets`,
  `Perte` (`optional="hide"`), rouge sous 90 %, orange entre 90 et 95 %.
- **Recherche** : « Rendement < 90 % », « < 95 % », « Unités incompatibles »,
  regroupement par état. Bornés à `state = 'done'`.
- **Catégorie de produit** : case « Sous-produit déchet ».

## Configuration après installation

Cocher `is_waste_byproduct` sur les catégories de déchets. Aucune catégorie n'est
cochée par défaut : sans configuration, `yield_useful_pct == material_yield` et
`yield_waste_qty == 0`.

Sur la base SOPROMER, la catégorie à cocher est « Dechet » (`product.category`
id 86). Le produit `DECHETS` n'a **jamais** été vendu — contre-épreuve sur 5
tests indépendants (`sale_order_line`, `pos_order_line`, `account_move_line`,
`purchase_order_line`, mouvements vers `usage=customer`), 0 Ar sur toute la vie
de la base, alors que les 4 dérivés `DRDPV*` totalisent 7 438 797 Ar sur 12 mois.
4 685,590 kg sont sortis en rebut.

⚠ **Passer par l'ORM, jamais en SQL.** `is_waste_byproduct` est dans le
`@api.depends` de `_compute_material_yield`
(`move_finished_ids.product_id.categ_id.is_waste_byproduct`). Par l'ORM,
l'invalidation se propage et les OF concernés se recalculent seuls. Par `UPDATE`
SQL, les colonnes `yield_*` resteraient figées sur leurs anciennes valeurs, sans
aucun signal.

Les régularisations de données sont portées **hors de ce module**, par le
module de régularisation dédié — elles ciblent des enregistrements par id,
propres à une base, et n'ont donc pas leur place dans un addon installable.

### Pourquoi les catégories sont configurables — et non corrigées

C'est une décision assumée, pas un contournement.

La migration Sage a auto-créé une catégorie d'UdM « Unsorted/Imported Units »
(`uom_uom.py:205-207`) contenant une unité nommée « kg », facteur 1.0, utilisée
par `EN_SEL03`, `FLCAL`, `PEFMIC11`, `DK00000021/400MGEZ`. Les 9 OF de recette
boulettes présentent donc **3** catégories d'UdM au lieu de 2, et resteraient
bloqués en `uom_mixed`.

La correction évidente — reclasser cette unité dans Poids — **est impossible**.
`stock/models/product.py:1126-1155` étend `uom.uom.write()` :

```python
if 'factor' in values or 'factor_inv' in values or 'category_id' in values:
    ...
    if self.env['stock.quant'].sudo().search_count([
        ('product_id.product_tmpl_id.uom_id', 'in', changed.ids),
        ('quantity', '!=', 0),
    ]):
        raise UserError(error_msg)
```

Les trois `search_count` sont en `sudo()`, aucune clé de contexte ne les
désarme. 13 quants non nuls existent, dont du stock réel en entrepôt. Ce
garde-fou protège le grand livre de stock et il a raison de le faire — les
contournements possibles (ajustement d'inventaire, SQL direct, `super()`) ont
tous été écartés pour cette raison.

Piège à connaître : le message d'erreur parle de « ratio », alors que la
condition d'entrée inclut `category_id`. Un simple reclassement déclenche donc
une erreur qui évoque un changement de facteur — ce qui a fait croire un temps
que le blocage était contournable.

Le problème n'est pas non plus historique : le produit garde cette UdM, donc
**chaque futur OF** utilisant ce composant serait privé de rendement.

D'où le flag `yield_mass_equivalent` : on ne corrige pas la donnée, on rend le
module configurable. Même patron que `is_waste_byproduct` — déclaratif,
explicite, réversible, sans effet de bord sur le stock.

### Pourquoi un script de migration accompagne les données

`data/uom_category_data.xml` porte `noupdate="1"` et cible des xmlid qui
appartiennent au module `uom`. En mode **update** (`-u`), `convert.py:341-350`
saute alors le nœud purement et simplement :

```python
if self.noupdate and self.mode != 'init':
    if record := env['ir.model.data']._load_xmlid(xid):
        ...
        return None
```

Les xmlid existant déjà, rien n'est écrit : sur une base existante le flag
restait `NULL`, y compris sur Poids et Volume. Le `noupdate="1"` reste correct
— il fait son travail à l'installation neuve et protège les choix
d'administrateur — mais il doit être complété par
`migrations/18.0.1.8.0/post-migrate.py`.

Ce script ne coche que les catégories dont la valeur est `NULL`, jamais celles
à `False` : un administrateur qui a délibérément décoché ne doit pas se la voir
recocher. La distinction n'est possible qu'en SQL (`IS NULL`) — via l'ORM un
Boolean rend `False` dans les deux cas. La lecture est donc en SQL, mais
**l'écriture passe par l'ORM**, pour déclencher le recalcul des rendements.

**Commande de configuration** pour la catégorie parasite, à passer séparément
(son id n'est pas portable, elle n'a pas d'xmlid) :

```python
env['uom.category'].browse(7).write({'yield_mass_equivalent': True})
```

⚠ Par l'ORM, jamais en SQL : le flag est dans le `@api.depends` de
`_compute_material_yield`
(`move_*.product_uom.category_id.yield_mass_equivalent`), donc les OF concernés
se recalculent seuls. Un `UPDATE` SQL laisserait les colonnes `yield_*` figées.

## Reprise des OF historiques

**Aucun script de recompute nécessaire.** `odoo/models.py:3468-3481` : `_auto_init`
détecte les nouvelles colonnes stockées calculées, fait un `SELECT id FROM
mrp_production` sans filtre et marque tous les enregistrements pour calcul.

Contrepartie : une seule transaction sur toute la table. **Installer hors heures de
production.**

Recalcul nécessaire en revanche après avoir coché `is_waste_byproduct` : le
`@api.depends` couvre le changement de flag, donc les OF concernés se recalculent
automatiquement à l'écriture. Aucune action manuelle.

`stock.move.yield_pct` étant non stocké, il n'entraîne ni `ALTER TABLE` ni recompute
sur la table la plus volumineuse de la base.

## Tests

```bash
odoo-bin -d <base> -u mrp_material_yield --test-enable \
         --test-tags /mrp_material_yield --stop-after-init
```

17 tests dans `tests/test_material_yield.py`. Couverture : OF nominal
(`US/MO/01877`), non-recomposition des pourcentages arrondis, Σ lignes =
en-tête, `no_byproduct`, `no_input`, OF annulé, `uom_mixed`, `approx`,
composant à 3 décimales, `over_100`, seuil relatif, move non `picked`, drapeau
déchet, et trois cas sur `yield_mass_equivalent` : trois catégories dont une non
cochée → `uom_mixed`, les trois cochées → `approx` à 94,68 %, et réversibilité du
flag (décocher rebascule en `uom_mixed`).

Le premier (`test_00_declared_fields_match_constants`) est un **contrôle
statique ensemble-contre-ensemble** : il compare les champs déclarés
`compute="_compute_material_yield"` dans le registre ORM (**11** — les 9
numériques plus `yield_uom_id` et `yield_state`) à ce que couvrent les
constantes de classe. Il attrape le cas résiduel « champ ajouté au chemin
nominal mais absent des constantes », qui ne casserait que
`_set_yield_undefined` — emprunté par tout OF annulé, soit 132 OF sur 1824 — et
ferait donc échouer l'upgrade via le backfill, sans qu'aucun test nominal ne le
voie.

## Internationalisation

`i18n/mrp_material_yield.pot` — 52 entrées : libellés et aides de champs,
valeurs de `yield_state`, chaînes `_()` des avertissements, libellés de vues.
Libellés source en français (langue de travail du client).

Les références de code y sont normalisées à `:0`, comme le fait Odoo lui-même
(`tools/translate.py:960-964`, *« lineno is set to 0 to avoid creating diff in
PO files every time the code is moved around »*) : le fichier ne bouge donc pas
quand le code est réorganisé.

Régénération après toute modification de libellé :

```bash
odoo-bin -d <base> --i18n-export=i18n/mrp_material_yield.pot \
         --modules=mrp_material_yield --stop-after-init
```

## Compatibilité

- Odoo 18 Community et Enterprise.
- Ne modifie ni `mrp_byproduct_costshare_sale_price`, ni
  `mrp_byproduct_costshare_autobranch`, ni `mrp_dashboard`.
- Aucun `sudo()`. Aucun modèle nouveau, donc pas de `ir.model.access.csv`.
- Aucune donnée envoyée à un service externe.

## Licence

LGPL-3
