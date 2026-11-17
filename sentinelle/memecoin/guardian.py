"""Module B — veto anti-arnaque (« Rug Pull Guardian ») : VETO, ABSTENTION ou PASSE, avec ses motifs.

Règles fixées d'avance (aucun réglage sur les résultats) et appliquées dans cet ordre :
1. donnée essentielle absente ou contradictoire → ABSTENTION (jamais un feu vert par défaut) ;
2. un seul signal de danger → VETO ;
3. signaux faibles → ABSTENTION ;
4. sinon → PASSE.

PASSE ne veut dire ni « sûr » ni « rentable » : un contrôle public ne voit pas tout (portefeuilles liés, vente concertée,
modification future d'un programme). Aucun score ne garantit l'absence d'arnaque.
"""
from __future__ import annotations

VETO, ABSTAIN, PASS = "VETO", "ABSTENTION", "PASSE"

RULES = {
    "min_liquidity_usd": 20_000.0,  # en dessous, un ordre de 10 € bouge le prix et une sortie peut devenir impossible
    "max_round_trip_loss": 0.10,  # aller-retour mesuré (achat puis revente immédiate) au-delà de 10 % : non revendable en pratique
    "max_top10_share": 0.35,  # 10 plus gros détenteurs hors pools et verrous connus
    "max_top1_share": 0.15,
    "max_creator_share": 0.10,
    "min_age_minutes": 30.0,
    "max_abs_m5_change_pct": 50.0,  # mouvement de 5 minutes anormal : manipulation possible, données peu fiables
    "max_liquidity_gap": 0.50,  # désaccord GeckoTerminal ↔ DexScreener sur la liquidité
    "min_sells_h1_if_buys": 1,  # des achats sans aucune vente : revente peut-être bloquée
    "buys_for_sell_check": 10,
    "min_unique_ratio": 0.10,  # acheteurs+vendeurs distincts / transactions : sous 10 %, activité concentrée (lavage possible)
    "min_lp_locked_pct": 50.0,  # pools classiques : liquidité ni brûlée ni verrouillée → retrait possible à tout moment
}
BONDING_CURVE_LAUNCHPADS = ("pump_fun",)  # courbe de lancement : pas de jeton de liquidité à verrouiller (règle 12 non applicable)
DANGER_RISKS_ALWAYS = ("Creator history of rugged tokens", "Freeze Authority still enabled", "Mint Authority still enabled", "Copycat token")


def assess(pool: dict, sec: dict | None, *, now_ms: int | None = None, rules: dict = RULES) -> dict:
    """``pool`` : enregistrement de pool (collecte) ; ``sec`` : enregistrement de sécurité du même mint, ou None."""
    veto, weak, missing = [], [], []
    now = now_ms or pool["observed_ms"]
    if sec is None:
        return _out(ABSTAIN, [], [], ["aucun contrôle de sécurité pour ce token"], pool)
    mi, rc, rt, ds = sec.get("mint_info"), sec.get("rugcheck"), sec.get("round_trip"), sec.get("dexscreener")
    if sec.get("invalid_mint"):
        return _out(VETO, ["adresse de mint invalide"], [], [], pool)
    # 1. données essentielles
    if not mi:
        missing.append("lecture du mint sur la chaîne indisponible")
    if not rc:
        missing.append("rapport de contrôle (détenteurs, créateur) indisponible")
    if rt is None:
        missing.append("aucun devis d'achat : revendabilité non mesurable")
    if pool.get("reserve_usd") is None:
        missing.append("liquidité inconnue")
    # 2. danger
    if mi:
        if not mi.get("exists", False):
            veto.append("mint introuvable sur la chaîne")
        if mi.get("mint_authority"):
            veto.append("émission encore possible (autorité d'émission active)")
        if mi.get("freeze_authority"):
            veto.append("gel des comptes possible (autorité de gel active)")
        if mi.get("transfer_fee") or mi.get("transfer_hook") or mi.get("permanent_delegate"):
            veto.append("extension Token-2022 à risque (frais de transfert, crochet ou délégué permanent)")
    if rc:
        if rc.get("rugged"):
            veto.append("déjà signalé comme arnaque (« rugged »)")
        for r in rc.get("risks") or []:
            if r["level"] == "danger" or r["name"] in DANGER_RISKS_ALWAYS:
                veto.append(f"signalement : {r['name']}")
        if (rc.get("transfer_fee_pct") or 0) > 0:
            veto.append("taxe de transfert")
        if rc.get("top10_share") is not None and rc["top10_share"] > rules["max_top10_share"]:
            veto.append(f"10 plus gros détenteurs : {rc['top10_share']:.0%} (> {rules['max_top10_share']:.0%})")
        if rc.get("top1_share") is not None and rc["top1_share"] > rules["max_top1_share"]:
            veto.append(f"plus gros détenteur : {rc['top1_share']:.0%} (> {rules['max_top1_share']:.0%})")
        if rc.get("creator_share") is not None and rc["creator_share"] > rules["max_creator_share"]:
            veto.append(f"le créateur détient {rc['creator_share']:.0%}")
        if rc.get("insider_networks"):
            weak.append(f"{rc['insider_networks']} réseau(x) de portefeuilles liés détecté(s)")
        if rc.get("launchpad") not in BONDING_CURVE_LAUNCHPADS and rc.get("lp_locked_pct_max") is not None \
                and rc["lp_locked_pct_max"] < rules["min_lp_locked_pct"]:
            veto.append(f"liquidité verrouillée ou brûlée : {rc['lp_locked_pct_max']:.0f} % seulement")
        if mi and rc.get("mint_authority") and not mi.get("mint_authority"):
            missing.append("autorité d'émission : la chaîne et le rapport se contredisent")
    if rt is not None:
        if rt.get("sell_unavailable"):
            veto.append("aucune route de revente : impossible à revendre via l'agrégateur")
        elif rt.get("loss") is not None and rt["loss"] > rules["max_round_trip_loss"]:
            veto.append(f"aller-retour immédiat : perte de {rt['loss']:.0%} (> {rules['max_round_trip_loss']:.0%})")
    if pool.get("reserve_usd") is not None and pool["reserve_usd"] < rules["min_liquidity_usd"]:
        veto.append(f"liquidité {pool['reserve_usd']:,.0f} $ (< {rules['min_liquidity_usd']:,.0f} $)".replace(",", " "))
    h1 = (pool.get("tx") or {}).get("h1") or {}
    if h1.get("buys", 0) >= rules["buys_for_sell_check"] and h1.get("sells", 0) < rules["min_sells_h1_if_buys"]:
        veto.append("des achats mais aucune vente depuis une heure : revente peut-être bloquée")
    # 3. signaux faibles
    if pool.get("created_ms") and (now - pool["created_ms"]) / 60000 < rules["min_age_minutes"]:
        weak.append("pool trop récent : historique insuffisant")
    m5 = (pool.get("price_change_pct") or {}).get("m5")
    if m5 is not None and abs(m5) > rules["max_abs_m5_change_pct"]:
        weak.append(f"mouvement de {m5:+.0f} % en 5 minutes")
    n = h1.get("buys", 0) + h1.get("sells", 0)
    uniq = h1.get("buyers", 0) + h1.get("sellers", 0)
    if n >= 50 and uniq / n < rules["min_unique_ratio"]:
        weak.append("activité concentrée sur peu de portefeuilles (lavage possible)")
    if ds and ds.get("liquidity_usd") and pool.get("reserve_usd"):
        gap = abs(ds["liquidity_usd"] / pool["reserve_usd"] - 1)
        if gap > rules["max_liquidity_gap"]:
            missing.append(f"liquidité contradictoire entre sources ({gap:.0%} d'écart)")
    status = VETO if veto else ABSTAIN if (missing or weak) else PASS
    return _out(status, veto, weak, missing, pool)


def _out(status: str, veto: list, weak: list, missing: list, pool: dict) -> dict:
    return {"status": status, "mint": pool.get("mint"), "pool": pool.get("pool"), "veto": veto, "weak": weak, "missing": missing,
            "note": "PASSE ne garantit ni l'absence d'arnaque ni un gain" if status == PASS else None}
