"""Registre de couverture des capacités C001–C100 (dossier du propriétaire du 24 novembre 2026, Sprint 5).

Chaque capacité porte un statut vérifié dans le code de main, ses preuves (fichier et symbole : fonction, classe, méthode
ou constante, dont les lignes sont calculées sur le code courant), ses tests, ses dépendances, une hypothèse
falsifiable, son contrat de données résumé et un verdict. Le registre ne promeut rien : aucune capacité n'entre dans une
décision fictive sans approbation humaine. Toutes les capacités partent « À AUDITER » ; ce premier inventaire les audite.

Statuts : EXISTANTE_ET_TESTÉE, PARTIELLE, ABSENTE, BLOQUÉE_DONNÉES, EXPÉRIMENT_REJETÉ, EXPÉRIMENT_VALIDÉ_PROVISOIRE.

Deux contrôles distincts (correction de l'audit indépendant de #148) :
- référencement (« referenced ») : un test cité nomme un symbole cité ou importe son module. Nécessaire, pas suffisant ;
- exécution effective (« --execution ») : chaque fichier de test cité est rejoué sous traceur de lignes (sys.monitoring,
  Python 3.12) et on constate si le corps des fonctions citées a réellement tourné. Un statut EXISTANTE_ET_TESTÉE,
  PARTIELLE ou EXPÉRIMENT_REJETÉ dont l'exécution n'est pas constatée pour chaque fonction citée est marqué
  « couverture à confirmer ».

Écrire le registre : python -m sentinelle.scalp.registre --ecrire
Mesurer l'exécution effective des tests cités : python -m sentinelle.scalp.registre --execution
Vérifier les références : python -m sentinelle.scalp.registre
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "scalp-v1" / "registre-capacites.json"
STATUTS = ("EXISTANTE_ET_TESTÉE", "PARTIELLE", "ABSENTE", "BLOQUÉE_DONNÉES", "EXPÉRIMENT_REJETÉ", "EXPÉRIMENT_VALIDÉ_PROVISOIRE")
VERDICTS = ("utilité technique", "avantage historique non démontré", "rejet", "non évaluable", "à mesurer")

DOMAINES = {
    "actualites": ("Actualités et événements", "horodatage, source et effet mesuré après disponibilité effective"),
    "macro": ("Macroéconomie et inter-marchés", "publication antérieure et référence comparée sans fuite"),
    "regimes": ("Régimes de marché", "classement de régime et abstention hors échantillon"),
    "technique": ("Analyse technique", "indicateur causal et comparaison avec momentum simple"),
    "microstructure": ("Microstructure et volumes", "niveau de carnet, tailles et coûts réellement accessibles"),
    "onchain": ("On-chain et Memecoins", "token et pool identifiés, veto inchangé et revente vérifiée"),
    "execution": ("Exécution et sorties fictives", "règles d'exécution identiques au simulateur causal"),
    "risque": ("Gestion des risques", "invariants des plafonds de risque et scénarios extrêmes"),
    "apprentissage": ("Apprentissage et orchestration", "calibration temporelle, robustesse et absence de promotion automatique"),
    "preuves": ("Preuves et exploitation", "reproductibilité SHA et mesure statistique indépendante"),
}

# Second envoi du propriétaire (24 novembre 2026) : critère de preuve par famille, méthode commune, lots et objectifs.
CRITERES = {
    "actualites": "provenance, horodatages et réaction mesurée après disponibilité effective",
    "macro": "données disponibles à la date, sans révision rétrospective utilisée abusivement",
    "regimes": "classification et abstention hors échantillon",
    "technique": "causalité et comparaison au momentum simple",
    "microstructure": "profondeur et coûts effectivement accessibles",
    "onchain": "identité du token et du pool, revendabilité et maintien des vetos",
    "execution": "mêmes règles que le simulateur causal",
    "risque": "invariants de sécurité et scénarios extrêmes",
    "apprentissage": "calibration, robustesse et validation humaine",
    "preuves": "reproductibilité par SHA et mesure statistique indépendante",
}
METHODE = ("hypothèse et protocole préenregistrés ; données horodatées à leur disponibilité réelle ; validation chronologique ; "
           "référence identique en capital, coûts, période et univers ; ablation ; correction des essais multiples ; intervalles de "
           "confiance ; cas absents, périmés, dupliqués, contradictoires et incidents réseau testés")
# Valeur ajoutée ATTENDUE (objectif de recherche, jamais un avantage démontré).
VALEUR = {
    "actualites": "qualité des entrées et vetos d'événements",
    "macro": "contexte et vetos autour des publications à risque",
    "regimes": "régularité et abstention dans les régimes défavorables",
    "technique": "qualité des entrées par rapport au momentum simple (aucun avantage démontré par #146 et #147)",
    "microstructure": "coûts et liquidité de sortie réellement accessibles",
    "onchain": "sécurité des memecoins : identité, revendabilité, vetos",
    "execution": "réalisme des exécutions et des sorties fictives",
    "risque": "réduction des pertes extrêmes et de la pire baisse",
    "apprentissage": "fiabilité des décisions, sans promotion automatique",
    "preuves": "fiabilité des mesures et reproductibilité",
}
LOTS = {**{c: "1 — fiabilité des mesures" for c in ("C091", "C094", "C095", "C061", "C062", "C097", "C099")},
        **{c: "2 — actualités, régimes et abstention" for c in ("C001", "C003", "C005", "C006", "C007", "C021", "C024", "C026", "C030",
                                                               "C082", "C083", "C084")},
        **{c: "3 — microstructure, maîtrise des pertes et risques" for c in ("C043", "C046", "C048", "C067", "C074", "C079")}}

# (identifiant, titre, domaine, priorité) — tel que transmis par le propriétaire.
CATALOGUE = [
    ("C001", "Mesure des délais publication→réception→marché", "actualites", "HAUTE"),
    ("C002", "Classification automatique des événements", "actualites", "normale"),
    ("C003", "Association sûre des nouvelles aux actifs", "actualites", "HAUTE"),
    ("C004", "Notation de provenance et fiabilité des sources", "actualites", "normale"),
    ("C005", "Déduplication inter-sources", "actualites", "HAUTE"),
    ("C006", "Surprise chiffrée contre consensus antérieur", "actualites", "HAUTE"),
    ("C007", "Études de réactions aux événements", "actualites", "HAUTE"),
    ("C008", "Décroissance temporelle des alertes et vetos", "actualites", "normale"),
    ("C009", "Détection d'événements systémiques", "actualites", "normale"),
    ("C010", "Attente d'une réaction initiale et contrôle de liquidité", "actualites", "normale"),
    ("C011", "Surprises d'inflation", "macro", "normale"),
    ("C012", "Surprises emploi et révisions", "macro", "normale"),
    ("C013", "Décisions et communications des banques centrales", "macro", "normale"),
    ("C014", "Mouvements des rendements obligataires", "macro", "normale"),
    ("C015", "Contexte de force du dollar", "macro", "normale"),
    ("C016", "Indicateurs de stress actions, dont VIX", "macro", "normale"),
    ("C017", "Propagation sectorielle actions→crypto", "macro", "normale"),
    ("C018", "Flux publics de stablecoins", "macro", "normale"),
    ("C019", "Flux ETF horodatés", "macro", "normale"),
    ("C020", "Calendrier des événements à risque", "macro", "normale"),
    ("C021", "Classification tendance/range", "regimes", "HAUTE"),
    ("C022", "Breadth et participation des altcoins", "regimes", "normale"),
    ("C023", "Force de tendance ADX", "regimes", "normale"),
    ("C024", "Régimes de volatilité", "regimes", "HAUTE"),
    ("C025", "Détection des ruptures de régime", "regimes", "normale"),
    ("C026", "Corrélations dynamiques BTC/altcoins", "regimes", "HAUTE"),
    ("C027", "Funding rate en observation uniquement", "regimes", "normale"),
    ("C028", "Open interest et liquidations en observation", "regimes", "normale"),
    ("C029", "Effets horaires et calendaires", "regimes", "normale"),
    ("C030", "Régime d'abstention", "regimes", "HAUTE"),
    ("C031", "Pivots confirmés causalement", "technique", "normale"),
    ("C032", "Trendlines avec qualité d'ajustement", "technique", "normale"),
    ("C033", "Zones support/résistance", "technique", "normale"),
    ("C034", "Retest de cassure", "technique", "normale"),
    ("C035", "Identification des fausses cassures", "technique", "normale"),
    ("C036", "Amplitude des ranges", "technique", "normale"),
    ("C037", "Compression de volatilité", "technique", "normale"),
    ("C038", "Divergences RSI/prix", "technique", "normale"),
    ("C039", "Épuisement de mouvement", "technique", "normale"),
    ("C040", "Confluence multi-timeframe authentique", "technique", "normale"),
    ("C041", "Spread achat/vente mesuré", "microstructure", "normale"),
    ("C042", "Déséquilibre de carnet multi-niveaux", "microstructure", "normale"),
    ("C043", "Pression des transactions agressives", "microstructure", "HAUTE"),
    ("C044", "Volume delta cumulatif", "microstructure", "normale"),
    ("C045", "Instabilité des ordres affichés", "microstructure", "normale"),
    ("C046", "Impact du notional sur l'exécution", "microstructure", "HAUTE"),
    ("C047", "Glissement conditionnel à la volatilité", "microstructure", "normale"),
    ("C048", "Profondeur de revente exploitable", "microstructure", "HAUTE"),
    ("C049", "Anomalies compatibles avec le wash trading", "microstructure", "normale"),
    ("C050", "Contrôle de cohérence inter-plateformes", "microstructure", "normale"),
    ("C051", "Historique âge et activité du pool", "onchain", "normale"),
    ("C052", "Historique de concentration des détenteurs", "onchain", "normale"),
    ("C053", "Surveillance des verrous de liquidité", "onchain", "normale"),
    ("C054", "Routes et volumes de revente", "onchain", "normale"),
    ("C055", "Restrictions et frais de transfert", "onchain", "normale"),
    ("C056", "Flux des grands détenteurs", "onchain", "normale"),
    ("C057", "Retraits de liquidité rapides", "onchain", "normale"),
    ("C058", "Portefeuilles liés au créateur, preuves vérifiées", "onchain", "normale"),
    ("C059", "Performance point-in-time des smart wallets", "onchain", "normale"),
    ("C060", "Cohérence de prix et liquidité multi-pools", "onchain", "normale"),
    ("C061", "Frais point-in-time par instrument", "execution", "HAUTE"),
    ("C062", "Minimum de notional, lot et tick", "execution", "HAUTE"),
    ("C063", "Exécutions partielles simulées", "execution", "normale"),
    ("C064", "Modèles de latence et d'exécution", "execution", "normale"),
    ("C065", "Causalité stricte décision→cotation ultérieure", "execution", "normale"),
    ("C066", "Refus des cotations périmées", "execution", "normale"),
    ("C067", "Stops avec gaps et liquidité insuffisante", "execution", "HAUTE"),
    ("C068", "Prises de bénéfices partielles", "execution", "normale"),
    ("C069", "Limite de durée des positions", "execution", "normale"),
    ("C070", "Filtre d'avantage espéré net de coûts", "execution", "normale"),
    ("C071", "Frein perte quotidienne", "risque", "normale"),
    ("C072", "Frein perte hebdomadaire", "risque", "normale"),
    ("C073", "Exposition plafonnée par stratégie", "risque", "normale"),
    ("C074", "Exposition plafonnée par clusters corrélés", "risque", "HAUTE"),
    ("C075", "Dimensionnement sous incertitude plafonné", "risque", "normale"),
    ("C076", "Ciblage de volatilité sans levier", "risque", "normale"),
    ("C077", "Réduction d'exposition après drawdown", "risque", "normale"),
    ("C078", "Stress de liquidité", "risque", "normale"),
    ("C079", "Scénarios de pertes extrêmes", "risque", "HAUTE"),
    ("C080", "Réserve minimale de liquidités", "risque", "normale"),
    ("C081", "Étiquettes et features causales", "apprentissage", "normale"),
    ("C082", "Calibration probabiliste", "apprentissage", "HAUTE"),
    ("C083", "Abstention par incertitude", "apprentissage", "HAUTE"),
    ("C084", "Détection de dérive des données", "apprentissage", "HAUTE"),
    ("C085", "Spécialistes légers par régime", "apprentissage", "normale"),
    ("C086", "Déduplication des caractéristiques corrélées", "apprentissage", "normale"),
    ("C087", "Ensembles légers et benchmark", "apprentissage", "normale"),
    ("C088", "Analyse textuelle du contexte à la demande", "apprentissage", "normale"),
    ("C089", "Anomalies de marché et incertitude", "apprentissage", "normale"),
    ("C090", "Champion/challenger paper-only", "apprentissage", "HAUTE"),
    ("C091", "Univers point-in-time sans biais de survie", "preuves", "HAUTE"),
    ("C092", "Registre immuable d'essais et empreintes", "preuves", "normale"),
    ("C093", "Validation temporelle purgée avec embargo", "preuves", "normale"),
    ("C094", "Correction des essais multiples", "preuves", "HAUTE"),
    ("C095", "Tests d'ablation par capacité", "preuves", "HAUTE"),
    ("C096", "Intervalles de confiance robustes avec dépendance temporelle", "preuves", "normale"),
    ("C097", "Stress frais/spread/glissement", "preuves", "HAUTE"),
    ("C098", "Faisabilité avec capital fictif de 30 € et produits UE", "preuves", "normale"),
    ("C099", "Écart entre simulation et cotation accessible", "preuves", "HAUTE"),
    ("C100", "Observation prospective figée sur 52 semaines, dont le démarrage exige une autorisation distincte", "preuves", "HAUTE"),
]

S = "sentinelle/"
FLASH, NEWS, PARS, RISK, SHIELD = S + "scalp/flash.py", S + "news/service.py", S + "news/shadow/parsers.py", S + "risk/manager.py", S + "risk/shield.py"
SIG, CORE, RECH, ESS, MOT = S + "scalp/signaux.py", S + "scalp/core.py", S + "scalp/recherche.py", S + "scalp/essais.py", S + "scalp/moteur.py"
SIM, STRAT = S + "dmh/memecoin_simulator.py", S + "dmh/memecoin_strategies.py"
REG, CAL, EVT, RSK, TEC = S + "scalp/regimes.py", S + "scalp/calibration.py", S + "scalp/evenements.py", S + "scalp/risques.py", S + "scalp/techniques.py"
MAC, FLX, EPU, APP = S + "scalp/macro.py", S + "scalp/flux.py", S + "scalp/epuisement.py", S + "scalp/apprentissage.py"
FRA, FAI, OUV, ABL = S + "scalp/frais.py", S + "scalp/faisabilite.py", S + "scalp/ouverture.py", S + "scalp/ablation.py"

# Audit : statut, preuves [(fichier, symbole)], tests, dépendances, hypothèse falsifiable, données, verdict, note.
E = {}


def entry(cid, statut, preuves, tests, dep, hypothese, donnees, verdict, note=""):
    E[cid] = {"statut": statut, "preuves": preuves, "tests": tests, "dependances": dep, "hypothese": hypothese,
              "donnees": donnees, "verdict": verdict, "note": note}


# --- Actualités et événements --------------------------------------------------------------------------------------------
entry("C001", "PARTIELLE", [(FLASH, "Archive.latency"), (FLASH, "poll"), (FLASH, "Archive.add")], ["tests/test_scalp_flash.py"], [],
      "Le délai publication→réception est mesurable par source ; le délai réception→réaction du prix ne l'est pas encore.",
      "8 sources publiques, archive SQLite en ajout seul depuis le 23 novembre 2026 ; veille bornée de 20 minutes publiée.",
      "à mesurer", "réception→marché absent ; archive trop jeune")
entry("C002", "EXISTANTE_ET_TESTÉE", [(FLASH, "classify"), (FLASH, "RULES"), (NEWS, "validate_analysis"), (PARS, "event_type")],
      ["tests/test_scalp_flash.py", "tests/test_news_telegram.py", "tests/test_macro_shadow.py"], [],
      "Le classement par mots-clés (et l'IA locale à la demande) reproduit les catégories d'un échantillon annoté.",
      "Titres et résumés des flux publics ; aucun échantillon annoté pour mesurer l'exactitude.", "utilité technique",
      "exactitude du classement non mesurée ; faux positif connu (ancien piratage Binance)")
entry("C003", "PARTIELLE", [(FLASH, "classify"), (NEWS, "NewsService._allowed_assets"), (S + "dmh/tokens.py", "TokenRegistry")],
      ["tests/test_scalp_flash.py", "tests/test_dmh_components.py", "tests/test_realtime.py"], ["C002"],
      "L'association par symbole confond des actifs (BTC cité dans une dépêche Binance) ; une association par identité vérifiée réduit les vetos erronés.",
      "Symboles dans les titres ; identité réseau+adresse côté memecoins (dmh).", "à mesurer", "association par symbole seulement dans flash.py")
entry("C004", "PARTIELLE", [(S + "news/signals.py", "SignalWatcher"), (S + "news/signals.py", "simulate_signal")],
      ["tests/test_new_features.py", "tests/test_realtime.py"], [],
      "Une note de fiabilité par source (exactitude, délai, doublons) prédit la justesse des alertes futures.",
      "Fiabilité mesurée seulement pour les canaux Telegram en observation, pas pour les flux d'actualité.", "à mesurer")
entry("C005", "PARTIELLE", [(NEWS, "similarity"), (NEWS, "NewsService._copy_from_cluster"), (PARS, "cluster_key"), (PARS, "dependent"), (FLASH, "Archive.add")],
      ["tests/test_news_telegram.py", "tests/test_macro_shadow.py", "tests/test_scalp_flash.py"], ["C002"],
      "Regrouper une même nouvelle reprise par plusieurs sources évite de compter plusieurs fois un événement et de prolonger un veto.",
      "flash.py ne déduplique qu'à l'intérieur d'une source (identifiant ou lien).", "à mesurer", "pas de déduplication inter-sources dans flash.py")
entry("C006", "PARTIELLE", [(PARS, "surprise_at"), (PARS, "bls_series"), (PARS, "difference")], ["tests/test_macro_shadow.py"], ["C011", "C012"],
      "La surprise (publié − consensus connu avant publication) explique la réaction du marché mieux que la valeur publiée seule.",
      "Calendrier public (consensus) et séries BLS officielles, en observation fantôme ; aucune mesure d'effet.", "à mesurer")
entry("C007", "EXISTANTE_ET_TESTÉE", [(EVT, "reaction"), (EVT, "paired_test"), (EVT, "events"), (EVT, "release_utc")], ["tests/test_scalp_evenements.py"], ["C001", "C020"],
      "Après une publication programmée (FOMC, CPI), la variation du BTC dépasse celle du même instant une semaine plus tôt ; son sens n'est pas prévisible sans la surprise.",
      "Calendriers officiels FOMC (federalreserve.gov) et CPI (bls.gov) 2018-2026, heures vérifiées sur les communiqués ; bougies 1 minute BTCUSDT.", "utilité technique",
      "research/scalp-v1/evenements-macro-v1.json : variation ×2 à ×3,5 dans les 5 minutes, Holm p < 0,001 ; sens non significatif")
entry("C008", "PARTIELLE", [(FLASH, "VETO_ASSET_MS"), (FLASH, "VETO_MARKET_MS"), (FLASH, "Archive.vetoes")], ["tests/test_scalp_flash.py"], ["C007"],
      "Un veto qui s'éteint progressivement fait moins d'entrées manquées qu'une fenêtre fixe, sans plus de pertes.",
      "Fenêtres fixes de 24 h et 6 h datées à la publication ; aucune décroissance.", "à mesurer")
entry("C009", "EXISTANTE_ET_TESTÉE", [(FLASH, "classify"), (FLASH, "Archive.vetoes"), (SHIELD, "Shield._major_news")],
      ["tests/test_scalp_flash.py", "tests/test_realtime.py"], ["C002"],
      "Les événements systémiques (macro, piratage majeur, faillite, décrochage) précèdent des baisses du marché entier.",
      "Classement par mots-clés et IA locale ; effet non mesuré.", "utilité technique")
entry("C010", "PARTIELLE", [(SHIELD, "Shield.check_entry"), (SHIELD, "Shield.microstructure"), (S + "risk/macro.py", "blackout")],
      ["tests/test_realtime.py"], ["C001", "C041"],
      "Attendre la première réaction et un carnet redevenu profond après un événement réduit le glissement et les entrées perdantes.",
      "Le bouclier bloque les nouvelles entrées pendant les à-coups ; pas d'attente explicite de réaction initiale.", "à mesurer")
# --- Macroéconomie et inter-marchés --------------------------------------------------------------------------------------
entry("C011", "PARTIELLE", [(PARS, "surprise_at"), (PARS, "bls_series")], ["tests/test_macro_shadow.py"], ["C006"],
      "Une inflation au-dessus du consensus précède une baisse des cryptos dans l'heure.", "Calendrier et BLS en observation fantôme.", "à mesurer")
entry("C012", "PARTIELLE", [(PARS, "surprise_at"), (PARS, "bls_series")], ["tests/test_macro_shadow.py"], ["C006"],
      "La surprise de l'emploi, révisions comprises, explique la réaction du marché.", "Révisions non suivies.", "à mesurer")
entry("C013", "PARTIELLE", [(FLASH, "SOURCES"), (S + "risk/macro.py", "events"), (S + "risk/macro.py", "blackout")],
      ["tests/test_scalp_flash.py", "tests/test_realtime.py"], ["C020"],
      "Les décisions et communiqués des banques centrales augmentent la volatilité ; une pause autour réduit les pertes.",
      "Flux de communiqués de la Fed ; fenêtre de pause macro ; effet non mesuré.", "à mesurer")
entry("C014", "EXPÉRIMENT_REJETÉ", [(MAC, "session_rules"), (MAC, "to_crypto_days"), (S + "scalp/actifs.py", "parse")], ["tests/test_scalp_macro.py", "tests/test_scalp_actifs.py"], [],
      "Une hausse rapide des rendements obligataires précède une baisse des actifs risqués.",
      "Rendement à 10 ans américain (^TNX, Yahoo, séance d → ligne crypto d) ; variation sur 20 séances, terciles connus à la date.", "rejet",
      "research/scalp-v1/macro-contexte-v1.json : M1 −0,10 %/sem sur B0, Holm 1 ; baisse 60 → 39 % comme son témoin décalé (42 %)")
entry("C015", "EXPÉRIMENT_REJETÉ", [(MAC, "session_rules"), (S + "market/context.py", "fetch_index")], ["tests/test_scalp_macro.py", "tests/test_realtime.py"], [],
      "Un dollar en hausse (DXY) précède une faiblesse des cryptos.", "Indice du dollar (DX-Y.NYB, Yahoo, horodaté à minuit de New York) ; rendement sur 20 séances.", "rejet",
      "research/scalp-v1/macro-contexte-v1.json : M2 −0,22 %/sem sur B0, Holm 1")
entry("C016", "EXPÉRIMENT_REJETÉ", [(MAC, "session_rules"), (S + "market/context.py", "fetch_index")], ["tests/test_scalp_macro.py", "tests/test_realtime.py"], [],
      "Un VIX élevé ou en hausse précède des baisses des cryptos.", "VIX (Yahoo) : niveau et variation sur 5 séances, terciles connus à la date.", "rejet",
      "research/scalp-v1/macro-contexte-v1.json : M3 −0,41 %/sem [−0,85 ; −0,03], M4 −0,19 %/sem sur B0, Holm 1")
entry("C017", "EXPÉRIMENT_REJETÉ", [(MAC, "lead_lag"), (MAC, "day_numbers")], ["tests/test_scalp_macro.py"], ["C014", "C016"],
      "Les secteurs actions liés (semi-conducteurs, valeurs crypto) mènent les cryptos.",
      "QQQ, SMH, MSTR (depuis le 11/08/2020), COIN (depuis le 14/04/2021) ; séance d contre BTC du jour d + 1.", "rejet",
      "research/scalp-v1/macro-contexte-v1.json : hypothèse contredite ; seul lien après Holm : légère inversion après le Nasdaq-100 (rang −0,056, Holm 0,032), non testée comme règle ; M5 −0,005 %/sem")
entry("C018", "EXPÉRIMENT_REJETÉ", [(FLX, "parse_supply"), (FLX, "usable"), (FLX, "supply_rule"), (FLX, "supply_lead")], ["tests/test_scalp_flux.py"], [],
      "Les créations et destructions de stablecoins précèdent les flux vers les cryptos.",
      "DefiLlama, offre en circulation indexée sur le dollar, NON point-in-time (borne optimiste) ; relevé utilisé le lendemain.", "rejet",
      "research/scalp-v1/flux-calendrier-v1.json : S1 −0,16 %/sem sur B0, Holm 1 ; offre sur 7 jours → BTC sur 7 jours : rang +0,06, p 0,13")
entry("C019", "BLOQUÉE_DONNÉES", [], [], [], "Les flux des ETF bitcoin précèdent le cours.",
      "Aucune source publique horodatée et à licence claire identifiée.", "non évaluable")
entry("C020", "EXISTANTE_ET_TESTÉE", [(S + "risk/macro.py", "blackout"), (S + "risk/macro.py", "upcoming"), (S + "market/context.py", "fetch_calendar"), (RISK, "RiskManager.evaluate_entry")],
      ["tests/test_realtime.py", "tests/test_risk_agent_backtest.py", "tests/test_new_features.py"], [],
      "Une pause autour des annonces majeures évite des pertes sans coût en rendement.", "Calendrier public ; effet non mesuré.", "utilité technique")
# --- Régimes de marché ---------------------------------------------------------------------------------------------------
entry("C021", "EXISTANTE_ET_TESTÉE", [(REG, "efficiency_ratio"), (REG, "regimes")], ["tests/test_scalp_regimes.py"], ["C023", "C024"],
      "Un classement tendance/range décidé sans regard en avant permet de s'abstenir en range.",
      "Efficacité de Kaufman du BTC sur 30 jours, seuil 0,3 fixé a priori.", "utilité technique", "research/scalp-v1/regimes-abstention-v1.json")
entry("C022", "EXPÉRIMENT_REJETÉ", [(TEC, "breadth")], ["tests/test_scalp_techniques.py"], ["C091"], "La part des altcoins en tendance (breadth) améliore le momentum simple.",
      "Part des membres de l'univers à rendement 28 jours positif.", "rejet", "research/scalp-v1/techniques-v2.json : −0,09 %/sem en filtre de B0, Holm 1")
entry("C023", "EXPÉRIMENT_REJETÉ", [(TEC, "adx")], ["tests/test_scalp_techniques.py"], [], "Un ADX élevé distingue les tendances exploitables.",
      "ADX de Wilder sur 14 jours ≥ 25.", "rejet", "research/scalp-v1/techniques-v2.json : −0,31 %/sem en filtre de B0 ; baisse réduite par moindre exposition")
entry("C024", "EXISTANTE_ET_TESTÉE", [(REG, "expanding_tercile"), (REG, "regimes"), (SHIELD, "Shield._evaluate_volatility")],
      ["tests/test_scalp_regimes.py", "tests/test_realtime.py"], [],
      "S'abstenir dans le tercile de volatilité haut (connu à la date) réduit les baisses mieux qu'un calendrier quelconque.",
      "Terciles de la volatilité réalisée du BTC sur 20 jours, fenêtre croissante.", "avantage historique non démontré",
      "research/scalp-v1/regimes-abstention-v1.json : A3 ne fait pas mieux que son témoin décalé")
entry("C025", "EXPÉRIMENT_REJETÉ", [(TEC, "regime_break"), (TEC, "recent")], ["tests/test_scalp_techniques.py"], ["C021", "C024"],
      "Une rupture de régime détectée tôt réduit la perte du début de baisse.", "Moyennes de rendements du BTC sur 20 et 120 jours, seuil de 2 erreurs types.", "rejet",
      "research/scalp-v1/techniques-v2.json : −0,44 %/sem [−0,81 ; −0,13], baisse 60 → 69 %")
entry("C026", "EXISTANTE_ET_TESTÉE", [(REG, "btc_correlation"), (S + "ai/knowledge.py", "compute_profile")], ["tests/test_scalp_regimes.py", "tests/test_learning.py"], [],
      "Une corrélation BTC/altcoins haute avec un BTC en baisse annonce un risque commun.", "Corrélation moyenne sur 30 jours des membres avec le BTC, terciles connus à la date.",
      "avantage historique non démontré", "research/scalp-v1/regimes-abstention-v1.json : A4 sans effet sur B0 ni B1")
entry("C027", "EXISTANTE_ET_TESTÉE", [(S + "market/context.py", "fetch_funding"), (S + "market/context.py", "funding_comment")],
      ["tests/test_realtime.py", "tests/test_cockpit_routes.py"], [], "Un taux de financement extrême précède des retournements.", "OKX public, observation seulement.", "utilité technique")
entry("C028", "PARTIELLE", [(SHIELD, "Shield.poll_liquidations"), (SHIELD, "Shield._evaluate_cascade")], ["tests/test_realtime.py"], [],
      "Une cascade de liquidations précède un excès de mouvement.", "Liquidations suivies ; open interest absent.", "à mesurer")
entry("C029", "PARTIELLE", [(FLX, "weekday"), (FLX, "month_turn"), (FLX, "weekend_rule"), (FLX, "mean_gap"), (FLX, "calendar"), (S + "scalp/features.py", "compute")],
      ["tests/test_scalp_flux.py", "tests/test_scalp_watchers.py"], [], "Certaines heures ou jours ont un rendement ou un coût différent.",
      "Jours de la semaine et tournant de mois du BTC (journalier) ; heures et coûts horaires NON MESURÉS (aucun carnet historique horodaté).",
      "rejet", "research/scalp-v1/flux-calendrier-v1.json : volet journalier seul ; aucun jour distinct après Holm (jeudi −0,53 pt, Holm 0,08) ; liquidités le week-end −0,67 %/sem sur B0")
entry("C030", "EXISTANTE_ET_TESTÉE", [(REG, "rules"), (REG, "abstain"), (REG, "shifted"), (SIG, "abstention")],
      ["tests/test_scalp_regimes.py", "tests/test_scalp_signaux.py"], ["C021", "C024"],
      "S'abstenir dans un régime défavorable défini d'avance améliore le rapport rendement/baisse mieux qu'un calendrier quelconque.",
      "4 règles fixées a priori, témoin décalé d'un an.", "avantage historique non démontré",
      "research/scalp-v1/regimes-abstention-v1.json : A1 (BTC en range) remplit le critère préenregistré (baisse de B0 60 → 23 %, −0,05 %/sem géométrique, non significatif, mieux que son témoin) ; historique déjà consulté et filtre proche de v10 : candidat soumis à validation humaine et prospective")
# --- Analyse technique ---------------------------------------------------------------------------------------------------
entry("C031", "EXPÉRIMENT_REJETÉ", [(TEC, "confirmed_pivot_high")], ["tests/test_scalp_techniques.py"], [], "Une clôture au-dessus du dernier sommet confirmé améliore le momentum simple.",
      "Sommet de 7 jours confirmé 3 jours après.", "rejet", "research/scalp-v1/techniques-v2.json : −0,32 %/sem ; baisse réduite par moindre exposition (16 % des jours)")
entry("C032", "EXPÉRIMENT_REJETÉ", [(SIG, "_trendline")], ["tests/test_scalp_signaux.py"], [],
      "Une ligne de tendance bien ajustée (pente > 0, R² ≥ 0,6) bat le momentum simple.",
      "signaux-v1 : écart +0,071 %/j, IC [−0,058 ; +0,206], non significatif.", "rejet", "research/scalp-v1/signaux-v1.json (F3)")
entry("C033", "EXPÉRIMENT_REJETÉ", [(SIG, "families")], ["tests/test_scalp_signaux.py"], ["C031"],
      "Les zones support/résistance améliorent les entrées.", "signaux-v1 : F4 (résistance 20 j) et F5 (support EMA 20) sans avantage.", "rejet",
      "zones par regroupement de pivots non testées ; research/scalp-v1/signaux-v1.json (F4, F5)")
entry("C034", "EXPÉRIMENT_REJETÉ", [(TEC, "retest")], ["tests/test_scalp_techniques.py"], ["C033"], "Entrer après le retest d'une cassure améliore le momentum simple.",
      "Retest à 1 % dans les 5 jours, signal 10 jours.", "rejet", "research/scalp-v1/techniques-v2.json : −0,42 %/sem")
entry("C035", "EXPÉRIMENT_REJETÉ", [(SIG, "_breakout_state"), (SIG, "families")], ["tests/test_scalp_signaux.py"], ["C043"],
      "Filtrer les cassures par le volume élimine les fausses cassures.", "signaux-v1 : F11, écart +0,038 %/j, IC [−0,060 ; +0,140].", "rejet",
      "research/scalp-v1/signaux-v1.json (F11)")
entry("C036", "EXISTANTE_ET_TESTÉE", [(EPU, "predictors"), (EPU, "daily_rank_ic"), (EPU, "amplitude_study")], ["tests/test_scalp_epuisement.py"], ["C024"],
      "L'amplitude du range prédit l'ampleur du mouvement suivant.", "Amplitude sur 20 jours contre volatilité réalisée sur 20 jours, rangs transversaux quotidiens des membres.",
      "utilité technique", "research/scalp-v1/epuisement-v1.json : aucune valeur ajoutée ; rang +0,265 contre +0,289 pour la volatilité réalisée, écart −0,024 (p 0,001)")
entry("C037", "EXPÉRIMENT_REJETÉ", [(S + "scalp/watchers.py", "detect"), (SIG, "families")], ["tests/test_scalp_watchers.py", "tests/test_scalp_signaux.py"], [],
      "Une compression de volatilité précède une expansion exploitable.", "signaux-v1 : F9 sans avantage ; veilleur « squeeze » du court terme, perdant après frais.", "rejet",
      "research/scalp-v1/signaux-v1.json (F9)")
entry("C038", "EXPÉRIMENT_REJETÉ", [(TEC, "bearish_divergence"), (TEC, "rsi")], ["tests/test_scalp_techniques.py"], [], "Écarter les divergences RSI baissières améliore le momentum simple.",
      "RSI(14) de Wilder, fenêtre de 20 jours.", "rejet", "research/scalp-v1/techniques-v2.json : −0,26 %/sem [−0,46 ; −0,07]")
entry("C039", "EXPÉRIMENT_REJETÉ", [(EPU, "capitulation"), (EPU, "hold"), (S + "scalp/watchers.py", "detect")], ["tests/test_scalp_epuisement.py", "tests/test_scalp_watchers.py"], [],
      "Un mouvement à bout de souffle (capitulation) se retourne.", "Chute ≤ −3 écarts types sur 5 jours et volume ≥ 2 × médiane, tenue 5 jours ; veilleur « capitulation » du court terme.", "rejet",
      "research/scalp-v1/epuisement-v1.json : E1 +0,016 %/sem, Holm 0,83, baisse 60 → 66 % ; E2 seule −0,010 %/sem géométrique, baisse 27 %")
entry("C040", "EXPÉRIMENT_REJETÉ", [(SIG, "families")], ["tests/test_scalp_signaux.py"], [],
      "L'accord des tendances hebdomadaire et journalière bat le momentum simple.", "signaux-v1 : F10, écart +0,023 %/j, IC [−0,057 ; +0,106].", "rejet",
      "research/scalp-v1/signaux-v1.json (F10)")
# --- Microstructure et volumes -------------------------------------------------------------------------------------------
entry("C041", "EXISTANTE_ET_TESTÉE", [(S + "costs/book.py", "measure"), (S + "dmh/observe.py", "Book.half_spread_bps")],
      ["tests/test_costs_measure.py", "tests/test_dmh_components.py", "tests/test_costs_engine.py"], [], "Le coût d'aller-retour mesuré dépasse l'hypothèse fixe des backtests pour certains marchés.",
      "Carnets publics datés ; instantané du 23 novembre 2026 publié.", "utilité technique")
entry("C042", "ABSENTE", [(S + "dmh/observe.py", "Book")], ["tests/test_dmh_components.py"], ["C041"], "Le déséquilibre du carnet sur plusieurs niveaux prédit le mouvement à très court terme.",
      "Carnets disponibles en direct ; aucun historique de carnet archivé.", "à mesurer")
entry("C043", "PARTIELLE", [(SIG, "families"), (S + "scalp/features.py", "compute")], ["tests/test_scalp_signaux.py", "tests/test_scalp_lab.py", "tests/test_scalp_watchers.py"], [],
      "La part des achats au marché prédit le rendement suivant.",
      "signaux-v1 F6 : p brute 0,0125, Holm 0,137 (#147) : non significatif après correction.", "avantage historique non démontré")
entry("C044", "EXPÉRIMENT_REJETÉ", [(SIG, "families")], ["tests/test_scalp_signaux.py"], ["C043"], "Le delta de volume cumulé ajoute à la pression acheteuse.",
      "Couvert par F6 (part des achats au marché sur 7 jours) : non redéveloppé.", "rejet",
      "research/scalp-v1/signaux-v1.json et research/scalp-v1/ablation-v1.json : F6 non significative après correction, nuisible en filtre de B0")
entry("C045", "BLOQUÉE_DONNÉES", [], [], ["C042"], "Les ordres affichés qui disparaissent signalent un faux carnet.", "Exige un flux de carnet en continu, non archivé.", "non évaluable")
entry("C046", "EXISTANTE_ET_TESTÉE", [(S + "execution/liquidity.py", "simulate_taker"), (S + "costs/book.py", "measure"), (S + "dmh/router.py", "walk")],
      ["tests/test_liquidity_simulation.py", "tests/test_costs_measure.py", "tests/test_dmh_components.py", "tests/test_costs_engine.py"], ["C041"],
      "Le coût d'un ordre croît avec sa taille selon la profondeur du carnet.", "Carnets publics datés (sondes).", "utilité technique")
entry("C047", "PARTIELLE", [(S + "costs/measure.py", "stress_states")], ["tests/test_costs_measure.py"], ["C041", "C024"],
      "Le glissement augmente avec la volatilité.", "États de stress mesurés ; pas de modèle de glissement en fonction de la volatilité dans les backtests.", "à mesurer")
entry("C048", "EXISTANTE_ET_TESTÉE", [(S + "dmh/established_pools.py", "round_trip"), (S + "costs/inventory.py", "round_trip"), (S + "execution/liquidity.py", "simulate_taker")],
      ["tests/test_memecoin_sellability.py", "tests/test_dmh_scanner.py", "tests/test_costs_engine.py"], ["C046"],
      "Une position dont la revente est vérifiée perd moins à la sortie.", "Devis Jupiter sans portefeuille ; carnets publics.", "utilité technique")
entry("C049", "ABSENTE", [], [], [], "Des volumes compatibles avec des échanges fictifs faussent l'univers défini par le volume.", "Non implémenté.", "à mesurer",
      "concerne directement l'univers à la date (C091)")
entry("C050", "EXISTANTE_ET_TESTÉE", [(S + "market/crosscheck.py", "CrossChecker.check"), (S + "dmh/observe.py", "consensus")],
      ["tests/test_new_features.py", "tests/test_dmh_components.py"], [], "Un écart de prix entre plateformes signale une donnée erronée.", "Deuxième plateforme publique.", "utilité technique")
# --- On-chain et Memecoins -----------------------------------------------------------------------------------------------
entry("C051", "PARTIELLE", [(S + "dmh/established_pools.py", "MIN_AGE_MINUTES"), (S + "dmh/established_pools.py", "prefilter")],
      ["tests/test_memecoin_engine.py", "tests/test_memecoin_sellability.py"], [], "L'âge et l'activité du pool réduisent les pertes par arnaque.", "Instantané à la détection ; aucun historique.", "utilité technique")
entry("C052", "PARTIELLE", [(S + "dmh/tokenrisk.py", "_rugcheck_holders"), (S + "dmh/tokenrisk.py", "BENEFICIAL_MAX_TOP10"), (S + "dmh/solana_scanner.py", "scan_once")], ["tests/test_dmh_scanner.py", "tests/test_memecoin_engine.py"], [],
      "Une concentration croissante des détenteurs précède une baisse.", "Rugcheck, instantané ; aucun historique.", "utilité technique")
entry("C053", "PARTIELLE", [(S + "dmh/tokenrisk.py", "_rugcheck"), (S + "dmh/solana_scanner.py", "scan_once")], ["tests/test_dmh_scanner.py", "tests/test_memecoin_engine.py"], [],
      "Un verrou de liquidité expirant augmente le risque de retrait.", "Verrous connus exclus du calcul ; aucune surveillance dans le temps.", "utilité technique")
entry("C054", "EXISTANTE_ET_TESTÉE", [(S + "dmh/established_pools.py", "round_trip"), (S + "dmh/dex.py", "jupiter_quote")],
      ["tests/test_memecoin_sellability.py", "tests/test_memecoin_routing.py"], [], "Une route de revente vérifiée évite les pièges.", "Jupiter public.", "utilité technique")
entry("C055", "PARTIELLE", [(S + "dmh/tokenrisk.py", "_goplus_evm"), (S + "dmh/tokenrisk.py", "assess_candidate"), (S + "dmh/solana_scanner.py", "scan_once")],
      ["tests/test_dmh_scanner.py"], [], "Les restrictions et frais de transfert signalent un piège.", "GoPlus, Rugcheck ; Token-2022 refusé sauf métadonnées.", "utilité technique",
      "chemin EVM (_goplus_evm) exécuté par aucun test de la suite (rejeu sous traceur)")
entry("C056", "PARTIELLE", [(STRAT, "entry_signals"), (STRAT, "NAMES")], ["tests/test_memecoin_signals.py", "tests/test_memecoin_journal_whale.py"], ["C059"],
      "Les achats des grands détenteurs précèdent une hausse.", "Stratégie whale_tail en simulation memecoin.", "à mesurer")
entry("C057", "PARTIELLE", [(SIM, "valuation"), (SIM, "refresh_round_trip")], ["tests/test_memecoin_valuation.py"], ["C054"],
      "Un retrait rapide de liquidité se détecte à temps pour sortir.", "Revente revérifiée ; aucun détecteur dédié aux retraits.", "à mesurer")
entry("C058", "ABSENTE", [], [], [], "Des portefeuilles liés au créateur, prouvés sur la chaîne, signalent une arnaque.", "Non implémenté.", "à mesurer")
entry("C059", "EXISTANTE_ET_TESTÉE", [(S + "dmh/smart_wallets.py", "recency_weight"), (S + "dmh/smart_wallets.py", "HALF_LIFE_S")],
      ["tests/test_memecoin_calibrator.py", "tests/test_memecoin_engine.py", "tests/test_memecoin_journal_whale.py"], [], "Les portefeuilles performants à la date gardent un avantage.", "Allers-retours WSOL clos, échantillons finalisés bornés.", "à mesurer")
entry("C060", "PARTIELLE", [(S + "dmh/observe.py", "dex_vs_reference"), (S + "dmh/router.py", "build_routes")],
      ["tests/test_dmh_components.py", "tests/test_memecoin_routing.py"], [], "Un écart de prix entre pools signale une liquidité fragile.", "Comparaison à une référence ; pas de suivi multi-pools.", "à mesurer")
# --- Exécution et sorties fictives ---------------------------------------------------------------------------------------
entry("C061", "EXISTANTE_ET_TESTÉE", [(FRA, "fee_table"), (FRA, "replay"), (S + "costs/venues.py", "binance_spot_vip0")],
      ["tests/test_scalp_frais.py", "tests/test_costs_engine.py"], [],
      "Des frais datés par instrument changent le classement des stratégies par rapport à un frais constant.",
      "Barème officiel binance.com : remise BNB 50 % puis 25 %, BTC/USDT sans frais 2022-2023 ; 2019-2021 non attesté.", "utilité technique",
      "research/scalp-v1/frais-dates-v1.json : écart < 0,001 %/sem ; sans BNB −0,013 %/sem sur B0 ; classement inchangé")
entry("C062", "EXISTANTE_ET_TESTÉE", [(FAI, "check_entry"), (FAI, "study"), (S + "costs/venues.py", "parse_exchange_info"), (S + "costs/inventory.py", "round_trip"),
                                      (RISK, "RiskManager._minimum_qty")], ["tests/test_scalp_faisabilite.py", "tests/test_costs_engine.py", "tests/test_execution_contract.py"], [],
      "Les minima de montant, de lot et de pas rendent certaines positions impossibles avec un petit capital.",
      "Règles de marché publiques du jour appliquées à l'historique.", "utilité technique",
      "research/scalp-v1/faisabilite-petit-capital-v1.json : à 30 €, 4 % des entrées de B1 et 73 % de celles de B0 faisables")
entry("C063", "EXISTANTE_ET_TESTÉE", [(S + "costs/execution.py", "evaluate_sample"), (S + "execution/liquidity.py", "simulate_taker")],
      ["tests/test_costs_engine.py", "tests/test_liquidity_simulation.py"], ["C046"], "Les exécutions partielles réduisent le gain réalisable.", "Ruban de transactions public.", "utilité technique")
entry("C064", "EXISTANTE_ET_TESTÉE", [(S + "costs/execution.py", "evaluate_sample"), (S + "dmh/observe.py", "Book.build")],
      ["tests/test_costs_engine.py", "tests/test_dmh_components.py"], [], "La latence dégrade le prix d'exécution.", "Ruban public, latence simulée.", "utilité technique")
entry("C065", "EXISTANTE_ET_TESTÉE", [(SIM, "_causal"), (SIM, "execution_check"), (S + "execution/contract.py", "signal_time"), (CORE, "run")],
      ["tests/test_memecoin_simulator.py", "tests/test_execution_contract.py", "tests/test_scalp_signaux.py", "tests/test_memecoin_engine.py", "tests/test_scalp_core.py"], [],
      "Une décision ne s'exécute jamais sur la cotation qui l'a déclenchée.", "Cotations datées ; ouverture suivante dans les backtests.", "utilité technique")
entry("C066", "EXISTANTE_ET_TESTÉE", [(S + "dmh/observe.py", "MAX_AGE_MS"), (S + "dmh/observe.py", "Book.fresh")],
      ["tests/test_dmh_components.py", "tests/test_memecoin_quote_time.py"], [], "Refuser une cotation trop ancienne évite des exécutions irréalistes.", "Âge de la cotation mesuré.", "utilité technique")
entry("C067", "PARTIELLE", [(S + "scalp/trade.py", "manage"), (RSK, "stop_gaps"), (S + "execution/contract.py", "stop_fill")],
      ["tests/test_scalp_risques.py", "tests/test_scalp_watchers.py", "tests/test_execution_contract.py"], ["C046"],
      "Un stop franchi par un écart s'exécute à l'ouverture ; en krach, la liquidité insuffisante aggrave la perte.",
      "Stops minute du moteur court terme (7 624) ; volume de la minute de sortie non conservé.", "utilité technique",
      "research/scalp-v1/risques-extremes-v1.json : 7,6 % des stops avec écart, +0,055 R en moyenne ; liquidité insuffisante non mesurée")
entry("C068", "EXISTANTE_ET_TESTÉE", [(STRAT, "ExitPolicy"), (STRAT, "exit_decision")], ["tests/test_memecoin_calibrator_stop.py", "tests/test_memecoin_simulator.py"], [],
      "Vendre une part à 1,25× puis par paliers améliore le rendement net.", "Simulation memecoin calibrée hors ligne.", "utilité technique")
entry("C069", "EXISTANTE_ET_TESTÉE", [(S + "scalp/trade.py", "manage"), (SIM, "time_exit")], ["tests/test_scalp_lab.py", "tests/test_memecoin_simulator.py", "tests/test_scalp_watchers.py"], [],
      "Une durée maximale limite les positions immobilisées.", "Règles de sortie.", "utilité technique")
entry("C070", "EXISTANTE_ET_TESTÉE", [(S + "costs/benefit.py", "net_edge"), (S + "scalp/brain.py", "PrincipalAI"), (S + "ai/model.py", "MetaModel.expected_r")],
      ["tests/test_costs_engine.py", "tests/test_scalp_lab.py", "tests/test_ai_core.py", "tests/test_scalp_desk.py", "tests/test_learning.py"], [],
      "N'entrer que si l'avantage espéré dépasse les coûts avec une marge améliore l'espérance nette.", "Coûts mesurés et hypothèses datées.", "avantage historique non démontré")
# --- Gestion des risques -------------------------------------------------------------------------------------------------
entry("C071", "EXISTANTE_ET_TESTÉE", [(RISK, "RiskManager.evaluate_entry"), (S + "scalp/desk.py", "run")], ["tests/test_risk_agent_backtest.py", "tests/test_scalp_desk.py"], [],
      "Le frein journalier n'est jamais dépassé et limite la perte d'un jour.", "Journal fictif.", "utilité technique")
entry("C072", "EXISTANTE_ET_TESTÉE", [(RISK, "RiskManager.evaluate_entry"), (RISK, "RiskManager.state")], ["tests/test_risk_agent_backtest.py"], [],
      "Le frein hebdomadaire (7 jours) n'est jamais dépassé.", "Journal fictif.", "utilité technique")
entry("C073", "PARTIELLE", [(RISK, "RiskManager.evaluate_entry"), (STRAT, "RiskProfile")], ["tests/test_risk_agent_backtest.py", "tests/test_memecoin_profiles.py"], [],
      "Un plafond par stratégie limite la concentration du risque.", "Positions maximales et budgets par stratégie memecoin ; pas de plafond par stratégie dans le moteur court terme.", "utilité technique")
entry("C074", "EXPÉRIMENT_REJETÉ", [(RSK, "cluster_cap"), (RSK, "top_k")], ["tests/test_scalp_risques.py"], ["C026"],
      "Plafonner l'exposition aux actifs corrélés réduit les baisses sans réduire le rendement net.",
      "Au plus 3 positions corrélées à plus de 0,8 sur 60 jours, témoin à nombre de positions égal.", "rejet",
      "research/scalp-v1/risques-extremes-v1.json : baisse de B0 60 → 66 %, pas mieux que son témoin")
entry("C075", "PARTIELLE", [(STRAT, "kelly"), (STRAT, "confidence"), (S + "costs/benefit.py", "net_edge")], ["tests/test_memecoin_calibrator_kelly.py", "tests/test_costs_engine.py"], [],
      "Une mise réduite quand l'incertitude est grande réduit les baisses.", "Kelly plafonné et confiance (memecoin) ; absent du moteur court terme.", "à mesurer")
entry("C076", "EXISTANTE_ET_TESTÉE", [(CORE, "run"), (SIG, "positions")], ["tests/test_scalp_core.py", "tests/test_scalp_signaux.py"], [],
      "Le ciblage de volatilité sans levier réduit la pire baisse à rendement par unité de risque égal ou meilleur.",
      "signaux-v1 : baisse 48 % → 19 % (vote) ; essais multiples #147 : DSR 0,947 seule, 0,975 avec fonds.", "avantage historique non démontré")
entry("C077", "EXISTANTE_ET_TESTÉE", [(RECH, "brake_returns"), (RISK, "RiskManager.evaluate_entry")], ["tests/test_scalp_recherche.py", "tests/test_risk_agent_backtest.py"], [],
      "Réduire l'exposition après une baisse de 15 % réduit la pire baisse.", "recherche-v1 : frein présent dans la grille, choisi certaines années.", "avantage historique non démontré")
entry("C078", "PARTIELLE", [(S + "costs/measure.py", "stress_states"), (S + "dmh/memecoin_calibrator.py", "MemecoinCalibrator")],
      ["tests/test_costs_measure.py", "tests/test_memecoin_calibrator.py"], ["C046"], "Une liquidité dégradée augmente le coût de sortie au-delà du plafond de risque.",
      "Stress mesurés (coûts) et banc memecoin ; pas de stress de liquidité du moteur court terme.", "à mesurer")
entry("C079", "EXISTANTE_ET_TESTÉE", [(RSK, "worst_windows"), (RSK, "window_return"), (SIG, "stability")], ["tests/test_scalp_risques.py", "tests/test_scalp_signaux.py"],
      ["C067", "C078"], "Les scénarios extrêmes restent sous le plafond de perte.",
      "10 pires semaines du BTC 2018-2026 et choc instantané de −30 % ; invariant sans levier.", "utilité technique",
      "research/scalp-v1/risques-extremes-v1.json : B1 ≥ −4,9 % sur les 10 pires semaines ; choc : B0 −30 %, B1 −10,3 % au pire")
entry("C080", "PARTIELLE", [(SIM, "_buy"), (STRAT, "RiskProfile")], ["tests/test_memecoin_profiles.py", "tests/test_memecoin_simulator.py"], [],
      "Une réserve minimale de liquidités n'est jamais entamée.", "Réserve par profil memecoin ; le moteur court terme peut être investi à 100 %.", "utilité technique")
# --- Apprentissage et orchestration --------------------------------------------------------------------------------------
entry("C081", "EXISTANTE_ET_TESTÉE", [(S + "ai/labeling.py", "label_candidates"), (S + "scalp/barriers.py", "simulate"), (S + "scalp/features.py", "compute")],
      ["tests/test_ai_core.py", "tests/test_scalp_lab.py", "tests/test_validation_leakage.py", "tests/test_scalp_watchers.py"], [],
      "Étiquettes et caractéristiques n'utilisent que le passé (aucun regard en avant).", "Bougies vérifiées.", "utilité technique")
entry("C082", "EXPÉRIMENT_REJETÉ", [(S + "ai/model.py", "MetaModel.fit_calibration"), (S + "scalp/brain.py", "PrincipalAI"), (CAL, "brier"), (CAL, "ece")],
      ["tests/test_scalp_calibration.py", "tests/test_scalp_desk.py", "tests/test_learning.py"], ["C093"],
      "Les probabilités calibrées hors échantillon sont plus fiables que le taux de gain appris.",
      "15 613 alertes jugées mois par mois (2024-01 → 2026-08).", "rejet",
      "research/scalp-v1/calibration-v1.json : compétence −3,3 % [−6,1 ; −0,5] ; recalage isotonique trop confiant aux extrêmes ; correction soumise à préenregistrement et validation humaine")
entry("C083", "EXISTANTE_ET_TESTÉE", [(S + "scalp/brain.py", "PrincipalAI"), (CAL, "abstention_gain")], ["tests/test_scalp_calibration.py", "tests/test_scalp_desk.py"], ["C082"],
      "S'abstenir quand les deux modèles de l'IA divergent améliore l'espérance des trades pris.",
      "Désaccord |arbres − linéaire| > 0,20 fixé a priori.", "avantage historique non démontré", "research/scalp-v1/calibration-v1.json : +0,017 R, non significatif")
entry("C084", "EXISTANTE_ET_TESTÉE", [(CAL, "psi"), (CAL, "walk_forward")], ["tests/test_scalp_calibration.py"], ["C082"],
      "Une dérive des données détectée tôt signale les mois où le modèle se dégrade.",
      "PSI par caractéristique (déciles de l'apprentissage).", "utilité technique",
      "research/scalp-v1/calibration-v1.json : dérive dans les 32 mois (variables de régime) ; lien avec la dégradation du Brier non significatif")
entry("C085", "EXPÉRIMENT_REJETÉ", [(APP, "judge_month"), (APP, "trend_of_previous_day"), (APP, "paired")], ["tests/test_scalp_apprentissage.py"], ["C021", "C024"],
      "Des spécialistes légers par régime battent un modèle unique.", "IA principale v4, spécialistes tendance/range (veille), repli sur le modèle unique fixé a priori.", "rejet",
      "research/scalp-v1/apprentissage-v1.json : Brier −0,0013 [−0,0071 ; +0,0038], Holm 0,39 ; compétence toujours négative")
entry("C086", "EXPÉRIMENT_REJETÉ", [(APP, "dedup_columns"), (APP, "masked"), (APP, "paired")], ["tests/test_scalp_apprentissage.py"], [],
      "Retirer les caractéristiques redondantes stabilise le modèle.", "Retrait glouton au-delà de 0,90 de corrélation de rang, sur l'apprentissage du mois seul.", "rejet",
      "research/scalp-v1/apprentissage-v1.json : Brier −0,0024 [−0,0059 ; +0,0002], Holm 0,12 ; stabilité mensuelle inchangée")
entry("C087", "EXISTANTE_ET_TESTÉE", [(S + "scalp/brain.py", "PrincipalAI"), (S + "ai/bench.py", "run_bench")], ["tests/test_scalp_desk.py", "tests/test_learning.py"], [],
      "Un ensemble léger bat le meilleur modèle seul et les règles simples.", "Banc d'essai synthétique et historique.", "avantage historique non démontré")
entry("C088", "EXISTANTE_ET_TESTÉE", [(NEWS, "OllamaClient"), (S + "dmh/llm.py", "draft_strategy"), (S + "dmh/llm.py", "is_loopback_url")], ["tests/test_news_telegram.py", "tests/test_dmh_components.py"], [],
      "L'analyse locale à la demande ajoute un contexte utile sans charger plusieurs modèles lourds.", "IA locale (Ollama), un seul modèle chargé.", "utilité technique")
entry("C089", "EXISTANTE_ET_TESTÉE", [(SHIELD, "Shield.check_entry"), (S + "market/crosscheck.py", "CrossChecker.check")], ["tests/test_realtime.py", "tests/test_new_features.py"], [],
      "Les anomalies de marché bloquent les entrées pendant l'incertitude.", "Relevés en direct.", "utilité technique")
entry("C090", "EXISTANTE_ET_TESTÉE", [(S + "services/champion.py", "register_pair"), (S + "services/champion.py", "RegisteredComparison"), (S + "services/models.py", "ModelStore.promote")],
      ["tests/test_champion_integration.py", "tests/test_production_evidence.py"], [],
      "La comparaison champion/challenger préenregistrée reste en simulation, sans promotion automatique.", "Prévisions appariées préenregistrées.", "utilité technique")
# --- Preuves et exploitation ---------------------------------------------------------------------------------------------
entry("C091", "EXISTANTE_ET_TESTÉE", [(RECH, "point_in_time_members"), (RECH, "cut_at_break"), (RECH, "splice"), (RECH, "link_chain"), (SIG, "members")],
      ["tests/test_scalp_recherche.py", "tests/test_scalp_signaux.py"], [],
      "Un univers défini à la date fait disparaître l'avantage des listes choisies après coup.",
      "42 candidates (disparues comprises), LUNA coupée (#145), 5 liens d'identité officiels vérifiés à la minute (#153) ; liste de candidates dressée en 2026.", "utilité technique",
      "#144 : v10 passe de +0,94 % à +0,28 %/semaine ; research/scalp-v1/continuite-actifs-v1.json : raccord non significatif")
entry("C092", "PARTIELLE", [(SIG, "load_bars"), (S + "scalp/data.py", "_fetch_verified")], ["tests/test_active_time_journal.py", "tests/test_configurable_capital.py", "tests/test_scalp_lab.py"], [],
      "Le registre des essais et les empreintes rendent chaque résultat rejouable et non modifiable.",
      "trials.jsonl en ajout seul par convention (aucun test ne l'impose) ; empreintes SHA-256 des archives ; gel #54/#55 testé.", "utilité technique")
entry("C093", "EXISTANTE_ET_TESTÉE", [(S + "ai/trainer.py", "train_walk_forward"), (RECH, "walk_forward"), (S + "scalp/brain.py", "PrincipalAI")],
      ["tests/test_learning.py", "tests/test_scalp_recherche.py", "tests/test_validation_leakage.py", "tests/test_scalp_desk.py"], [], "La validation purgée avec embargo empêche toute fuite.",
      "Fenêtres chronologiques purgées ; embargo d'un jour.", "utilité technique")
entry("C094", "EXISTANTE_ET_TESTÉE", [(ESS, "holm"), (ESS, "benjamini_hochberg"), (ESS, "deflated_sharpe"), (ESS, "bootstrap_pvalue")],
      ["tests/test_scalp_essais.py"], ["C096"], "Après correction des essais multiples, l'avantage apparent disparaît-il ?",
      "#147 : aucune des 11 familles significative à 5 % (Holm, BH) ; DSR publiés.", "utilité technique")
entry("C095", "EXISTANTE_ET_TESTÉE", [(ABL, "compare"), (ABL, "adjust"), (ABL, "weekly_pairs")], ["tests/test_scalp_ablation.py"], ["C094", "C096"],
      "Chaque capacité se compare avec et sans elle, à capital, univers, période et coûts identiques.",
      "Outil d'ablation générique : mêmes jours exigés, intervalle par blocs de 4 semaines, Holm.", "utilité technique", "research/scalp-v1/ablation-v1.json (#149)")
entry("C096", "EXISTANTE_ET_TESTÉE", [(SIG, "edge"), (ESS, "bootstrap_pvalue"), (S + "costs/stats.py", "cluster_bootstrap"), (S + "ai/metrics.py", "lower_bound")],
      ["tests/test_scalp_signaux.py", "tests/test_scalp_essais.py", "tests/test_costs_measure.py", "tests/test_ai_learning.py"], [], "Les intervalles par blocs tiennent compte de la dépendance temporelle.",
      "Blocs circulaires de 20 jours ; grappes de dates.", "utilité technique")
entry("C097", "EXISTANTE_ET_TESTÉE", [(ABL, "stress")], ["tests/test_scalp_ablation.py"], ["C041", "C061"],
      "Le classement des stratégies résiste au doublement des frais, de l'écart et du glissement.", "Niveaux × 1, × 2, × 3, × 3 + écart de 0,10 %.", "utilité technique",
      "research/scalp-v1/ablation-v1.json : B0 bat l'univers à chaque niveau ; B1 perd son avantage sur les fonds à × 3 + écart")
entry("C098", "PARTIELLE", [(FAI, "study"), (S + "costs/inventory.py", "round_trip"), (S + "dmh/ledger.py", "Ledger")],
      ["tests/test_scalp_faisabilite.py", "tests/test_dmh_components.py"], ["C062"],
      "Avec 30 € fictifs, les minima d'ordre et les frais fixes rendent la stratégie faisable ou non ; idem avec des fonds UCITS.",
      "Capitaux de 30 € à 10 000 € convertis au cours EUR/USDT du jour.", "utilité technique",
      "research/scalp-v1/faisabilite-petit-capital-v1.json : faisable à partir d'environ 1 000 € ; fonds UCITS NON MESURÉ")
entry("C099", "PARTIELLE", [(OUV, "adverse_gap"), (OUV, "draw"), (S + "scalp/reserve.py", "measured_slippage")], ["tests/test_scalp_ouverture.py", "tests/test_scalp_reserve.py"], ["C041"],
      "Le prix simulé (ouverture de la bougie) s'écarte du prix accessible d'un montant mesurable.",
      "250 exécutions par stratégie, archives minute ; aucun carnet historique.", "utilité technique",
      "research/scalp-v1/ecart-ouverture-v1.json : 1 à 1,7 pb en moyenne la 1re minute, p95 20 à 24 pb ; écart achat-vente NON MESURÉ")
entry("C100", "ABSENTE", [], [], ["C092", "C095"], "Une configuration figée garde son avantage sur 52 semaines d'observation fictive.",
      "Exige l'autorisation du propriétaire ; aucune observation démarrée.", "non évaluable", "décision du propriétaire")


# --- Résolution des lignes et vérification ------------------------------------------------------------------------------

def _nodes(path: Path) -> dict:
    """Nœuds des symboles d'un fichier Python : fonctions, classes, méthodes (Classe.méthode) et constantes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out[f"{node.name}.{sub.name}"] = sub
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                names = t.elts if isinstance(t, ast.Tuple) else [t]
                for n in names:
                    if isinstance(n, ast.Name):
                        out[n.id] = node
    return out


def _symbols(path: Path) -> dict:
    """Symboles d'un fichier Python avec leurs lignes (début, fin)."""
    return {name: (node.lineno, node.end_lineno) for name, node in _nodes(path).items()}


def _body_lines(node) -> set[int]:
    """Lignes des corps de fonction contenus dans un symbole. En-têtes, décorateurs, valeurs par défaut, docstrings et
    instructions de niveau classe sont exclus : ils s'exécutent dès l'import, sans qu'aucun test n'appelle le code.
    Vide pour une constante ou une structure sans méthode (« définition »)."""
    lines = set()
    for fn in ast.walk(node):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = fn.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            for stmt in body:
                lines.update(range(stmt.lineno, stmt.end_lineno + 1))
    return lines


def head_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build() -> dict:
    cache, items = {}, []
    for cid, titre, dom, prio in CATALOGUE:
        e = E[cid]
        preuves = []
        for fichier, symbole in e["preuves"]:
            if fichier not in cache:
                cache[fichier] = _symbols(ROOT / fichier)
            a, b = cache[fichier][symbole]
            preuves.append({"fichier": fichier, "symbole": symbole, "lignes": f"{a}-{b}"})
        items.append({"id": cid, "titre": titre, "domaine": DOMAINES[dom][0], "premiere_preuve_exigee": DOMAINES[dom][1], "priorite": prio,
                      "statut": e["statut"], "preuves": preuves, "tests": e["tests"], "dependances": e["dependances"],
                      "hypothese": e["hypothese"], "donnees": e["donnees"], "verdict": e["verdict"], "note": e["note"],
                      "methode_validation": f"{METHODE} ; critère de preuve : {CRITERES[dom]}", "lot": LOTS.get(cid, "suivants (dépendances et valeur marginale)"),
                      "valeur_ajoutee_attendue": f"{VALEUR[dom]} — attendue, non démontrée",
                      "integration_decision": "aucune sans approbation humaine"})
    counts = {s: sum(1 for i in items if i["statut"] == s) for s in STATUTS}
    return {"schema": "sentinelle-registre-capacites-v1", "source": "dossier du propriétaire C001–C100 (24 novembre 2026), docs/scalp/catalogue-C001-C100.md",
            "main_audite": head_sha(), "statuts": counts, "capacites": items}


def check(registry: dict, strict_lines: bool = True) -> list[str]:
    """Problèmes du registre : identifiants, statuts, verdicts, fichiers, symboles et lignes, tests et dépendances.
    Les lignes sont exactes au commit audité (« main_audite ») ; « strict_lines=False » tolère qu'elles aient bougé depuis
    (le symbole doit toujours exister), pour ne pas bloquer les PR qui modifient un fichier cité."""
    problems = []
    ids = [i["id"] for i in registry["capacites"]]
    if ids != [f"C{n:03d}" for n in range(1, 101)]:
        problems.append("identifiants : C001 à C100 attendus, dans l'ordre, sans doublon")
    cache = {}
    for i in registry["capacites"]:
        if i["statut"] not in STATUTS:
            problems.append(f"{i['id']} : statut inconnu {i['statut']}")
        if i["verdict"] not in VERDICTS:
            problems.append(f"{i['id']} : verdict inconnu {i['verdict']}")
        if i["statut"] in ("EXISTANTE_ET_TESTÉE", "PARTIELLE", "EXPÉRIMENT_REJETÉ") and not (i["preuves"] and i["tests"]):
            problems.append(f"{i['id']} : {i['statut']} sans preuve ni test")
        if i["preuves"] and i["tests"] and not referenced(i):
            problems.append(f"{i['id']} : aucun test cité ne référence les symboles ou modules cités")
        cov = i.get("couverture_effective")
        if cov is not None:
            if cov["niveau"] != coverage_level(cov["symboles"], bool(i["preuves"])):
                problems.append(f"{i['id']} : niveau de couverture incohérent avec les symboles mesurés")
            if cov["a_confirmer"] != (i["statut"] in CLAIMS_TESTED and cov["niveau"] != "constatée"):
                problems.append(f"{i['id']} : marque « couverture à confirmer » incohérente")
        if i["statut"] == "EXPÉRIMENT_REJETÉ" and "research/" not in i["note"]:
            problems.append(f"{i['id']} : rejet sans fichier de résultats cité")
        for p in i["preuves"]:
            path = ROOT / p["fichier"]
            if not path.exists():
                problems.append(f"{i['id']} : fichier absent {p['fichier']}")
                continue
            cache.setdefault(p["fichier"], _symbols(path))
            got = cache[p["fichier"]].get(p["symbole"])
            if got is None:
                problems.append(f"{i['id']} : symbole absent {p['fichier']}:{p['symbole']}")
            elif strict_lines and f"{got[0]}-{got[1]}" != p["lignes"]:
                problems.append(f"{i['id']} : lignes déplacées {p['fichier']}:{p['symbole']} ({p['lignes']} → {got[0]}-{got[1]})")
        for t in i["tests"]:
            if not (ROOT / t).exists():
                problems.append(f"{i['id']} : test absent {t}")
        for d in i["dependances"]:
            if d not in ids:
                problems.append(f"{i['id']} : dépendance inconnue {d}")
    return problems


def referenced(item: dict) -> bool:
    """Contrôle de RÉFÉRENCEMENT seulement : un test cité nomme un des symboles cités ou le nom de leur module. Cela ne
    prouve pas que le code est exécuté (un commentaire suffit) ; l'exécution se constate avec measure_execution()."""
    words = set()
    for p in item["preuves"]:
        words.add(p["symbole"].split(".")[-1])
        words.add(Path(p["fichier"]).stem)
    for t in item["tests"]:
        text = (ROOT / t).read_text(encoding="utf-8")
        if any(w in text for w in words):
            return True
    return False


# --- Exécution effective des tests cités -----------------------------------------------------------------------------

CLAIMS_TESTED = ("EXISTANTE_ET_TESTÉE", "PARTIELLE", "EXPÉRIMENT_REJETÉ")
NIVEAUX = ("constatée", "partielle", "non constatée", "référencement seul", "sans preuve")
TRACE_TIMEOUT = 1800


def _trace_run(out: str, tests: list[str]) -> int:
    """Dans un processus dédié : rejoue des fichiers de test sous traceur de lignes et écrit les lignes exécutées du
    paquet sentinelle (chaque ligne n'est enregistrée qu'une fois, puis l'évènement est coupé pour rester léger)."""
    import pytest

    mon = sys.monitoring
    tool = mon.COVERAGE_ID
    base = os.path.normcase(str(ROOT / "sentinelle")) + os.sep
    seen: dict[str, set[int]] = {}

    def on_line(code, line):
        f = os.path.normcase(os.path.abspath(code.co_filename))
        if f.startswith(base):
            seen.setdefault(f, set()).add(line)
        return mon.DISABLE

    mon.use_tool_id(tool, "sentinelle-registre")
    mon.register_callback(tool, mon.events.LINE, on_line)
    mon.set_events(tool, mon.events.LINE)
    try:
        code = int(pytest.main([*tests, "-p", "no:cacheprovider"]))
    finally:
        mon.set_events(tool, 0)
        mon.register_callback(tool, mon.events.LINE, None)
        mon.free_tool_id(tool)
    rel = {Path(f).relative_to(os.path.normcase(str(ROOT))).as_posix(): sorted(v) for f, v in seen.items()}
    Path(out).write_text(json.dumps({"code_pytest": code, "lignes": rel}), encoding="utf-8")
    return code


def trace(test: str) -> dict:
    """Rejoue un fichier de test dans un sous-processus isolé : code de sortie pytest, durée et lignes exécutées."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "trace.json"
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-m", "sentinelle.scalp.registre", "--tracer", str(out), test], cwd=ROOT,
                              capture_output=True, text=True, timeout=TRACE_TIMEOUT)
        if not out.exists():
            return {"code_pytest": proc.returncode, "secondes": round(time.time() - t0, 1), "lignes": {}, "erreur": proc.stderr[-500:]}
        run = json.loads(out.read_text(encoding="utf-8"))
        run["secondes"] = round(time.time() - t0, 1)
        return run


def coverage_level(symbols: list[dict], has_evidence: bool = True) -> str:
    """« constatée » : le corps de chaque fonction citée a tourné pendant un test cité réussi ; « partielle » : une partie ;
    « non constatée » : aucune ; « référencement seul » : seules des définitions (constantes, structures) sont citées."""
    if not has_evidence:
        return "sans preuve"
    funcs = [s for s in symbols if s["nature"] == "fonction"]
    if not funcs:
        return "référencement seul"
    hit = sum(bool(s["executee"]) for s in funcs)
    return "constatée" if hit == len(funcs) else "partielle" if hit else "non constatée"


def coverage_of(item: dict, runs: dict) -> dict:
    """Couverture effective d'une capacité : seuls les fichiers de test cités ET réussis (code pytest 0) comptent."""
    ok = [t for t in item["tests"] if runs.get(t, {}).get("code_pytest") == 0]
    symbols = []
    for p in item["preuves"]:
        body = _body_lines(_nodes(ROOT / p["fichier"])[p["symbole"]])
        executed = set().union(*(runs[t]["lignes"].get(p["fichier"], ()) for t in ok)) if ok else set()
        symbols.append({"fichier": p["fichier"], "symbole": p["symbole"], "nature": "fonction" if body else "définition",
                        "executee": bool(body & executed) if body else None})
    level = coverage_level(symbols, bool(item["preuves"]))
    return {"niveau": level, "symboles": symbols, "tests_en_echec": [t for t in item["tests"] if t not in ok],
            "a_confirmer": item["statut"] in CLAIMS_TESTED and level != "constatée"}


def measure_execution(registry: dict, workers: int = 4) -> dict:
    """Rejoue chaque fichier de test cité sous traceur et qualifie la couverture effective de chaque capacité."""
    tests = sorted({t for i in registry["capacites"] for t in i["tests"]})
    runs = {}
    with ThreadPoolExecutor(workers) as pool:
        for t, r in zip(tests, pool.map(trace, tests)):
            runs[t] = r
            print(f"{t} : code pytest {r['code_pytest']}, {r['secondes']} s", flush=True)
    for i in registry["capacites"]:
        i["couverture_effective"] = coverage_of(i, runs)
    levels = {n: sum(i["couverture_effective"]["niveau"] == n for i in registry["capacites"]) for n in NIVEAUX}
    registry["mesure_execution"] = {
        "methode": "chaque fichier de test cité rejoué seul sous sys.monitoring (évènements LINE) ; une fonction est exécutée si une "
                   "ligne de son corps a tourné pendant un fichier de test réussi ; en-têtes, décorateurs, docstrings et niveau "
                   "module exclus ; le code lancé dans un autre processus par un test n'est pas compté",
        "commit": head_sha(), "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(), "plateforme": f"{platform.system()} {platform.machine()}",
        "commande": "python -m sentinelle.scalp.registre --execution",
        "tests": {t: {"code_pytest": r["code_pytest"], "secondes": r["secondes"]} for t, r in runs.items()},
        "niveaux": levels,
        "statuts_a_couverture_a_confirmer": [i["id"] for i in registry["capacites"] if i["couverture_effective"]["a_confirmer"]]}
    return registry


# --- Empreintes du baseline -------------------------------------------------------------------------------------------

def commit_available(commit: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT, capture_output=True).returncode == 0


def check_fingerprints(base: dict) -> list[str]:
    """Empreintes figées comparées au contenu des modules AU COMMIT DU GEL (objets git), pas à l'arbre courant : un module
    peut évoluer ensuite sans invalider le gel, mais l'empreinte publiée doit être celle du code réellement mesuré."""
    problems = []
    for path, sha in base["modules_sha256"].items():
        proc = subprocess.run(["git", "cat-file", "blob", f"{base['main']}:{path}"], cwd=ROOT, capture_output=True)
        if proc.returncode != 0:
            problems.append(f"{path} : absent du commit {base['main']}")
        elif hashlib.sha256(proc.stdout).hexdigest() != sha:
            problems.append(f"{path} : empreinte différente du contenu au commit {base['main']}")
    return problems


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline() -> dict:
    """Référence figée pour les ablations : moteur, rendement géométrique net, baisse, coûts, transactions, semaines
    positives, tests et provenance, lus dans les fichiers de résultats publiés (aucun recalcul)."""
    res = ROOT / "research" / "scalp-v1"
    sig, mot = res / "signaux-v1.json", res / "moteur-unique-2018-2026.json"
    s, m = json.loads(sig.read_text(encoding="utf-8")), json.loads(mot.read_text(encoding="utf-8"))
    modules = {f: digest(ROOT / f) for f in (SIG, MOT, CORE, S + "scalp/data.py", S + "scalp/actifs.py", RECH)}

    def row(stats, name, cost, trades, tests, source):
        return {"nom": name, "rendement_hebdo_geometrique_net_pct": round(stats["geometric_week_pct"], 4),
                "rendement_annuel_net_pct": round(stats["annual_pct"], 2), "pire_baisse_pct": round(stats["max_drawdown_pct"], 2),
                "semaines_positives": stats["positive_weeks"], "semaines": stats["weeks"], "couts_par_cote": cost,
                "transactions": trades, "tests": tests, "provenance": source}

    sig_src = {"fichier": str(sig.relative_to(ROOT)), "sha256": digest(sig), "periode": "2018-01-01 → 2026-11-22 (historique consulté)"}
    vote = s["etape2"]["C_VOTE, volatilité visée 30 %"]
    u1 = m["rows"]["moteur U1 (crypto + memecoins + actions)"]["all"]
    u1_stats = {"geometric_week_pct": u1["compound_week_pct"], "annual_pct": u1["annual_pct"], "max_drawdown_pct": u1["max_drawdown_pct"],
                "positive_weeks": u1["positive_weeks"], "weeks": u1["weeks"]}
    return {"main": head_sha(), "modules_sha256": modules, "references": [
        row(s["etape1"]["F1_momentum"]["seule"], "B0 — momentum simple F1 (référence des capacités techniques)", s["couts"]["base_par_cote"],
            "NON MESURÉ (non publié par signaux-v1)", ["tests/test_scalp_signaux.py"], sig_src),
        row(vote["avec_fonds_35_65"], "B1 — vote 2 sur 3, volatilité visée 30 %, avec fonds 35/65 (seul DSR probable, #147)", s["couts"]["base_par_cote"],
            len(vote["transactions"]), ["tests/test_scalp_signaux.py", "tests/test_scalp_essais.py"], sig_src),
        row(u1_stats, "B2 — moteur unique U1 sur main (#143)", "crypto 0,075 % + glissement mesuré ; fonds 0,10 %",
            {"transferts_entre_poches": m["rows"]["moteur U1 (crypto + memecoins + actions)"]["transfers"],
             "ordres_poche_crypto": m["rows"]["poche crypto seule (crypto + memecoins)"].get("orders")},
            ["tests/test_scalp_moteur.py"], {"fichier": str(mot.relative_to(ROOT)), "sha256": digest(mot), "periode": "2018-01-01 → 2026-11-22 (historique consulté)"}),
    ], "resultats_negatifs_conserves": ["#140 test réservé −6,95 % « non confirmé »", "#146 verdict négatif (aucune stratégie à +1,30 %/semaine)",
                                        "#147 aucune famille significative à 5 % après correction"],
       "note": "aucune date d'achèvement des 100 capacités n'est fixée ; aucune période historique aveugle ne reste"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Registre de couverture des capacités C001–C100")
    ap.add_argument("--ecrire", action="store_true", help="réécrit research/scalp-v1/registre-capacites.json sur le code courant")
    ap.add_argument("--nouveau-baseline", action="store_true", help="refige le baseline au commit courant (décision explicite)")
    ap.add_argument("--execution", action="store_true", help="rejoue les tests cités sous traceur et écrit la couverture effective")
    ap.add_argument("--tracer", nargs="+", metavar=("SORTIE", "TEST"), help=argparse.SUPPRESS)  # usage interne de trace()
    args = ap.parse_args(argv)
    if args.tracer:
        return _trace_run(args.tracer[0], args.tracer[1:])
    if args.ecrire:
        previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
        registry = build()
        # Le baseline reste figé (SHA, empreintes, références) : seul « --nouveau-baseline » le refige, par décision explicite.
        registry["baseline"] = baseline() if args.nouveau_baseline or "baseline" not in previous else previous["baseline"]
        OUT.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.execution:
        registry = measure_execution(json.loads(OUT.read_text(encoding="utf-8")))
        OUT.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    registry = json.loads(OUT.read_text(encoding="utf-8"))
    problems = check(registry)
    print(json.dumps(registry["statuts"], ensure_ascii=False))
    if "mesure_execution" in registry:
        m = registry["mesure_execution"]
        print("couverture effective :", json.dumps(m["niveaux"], ensure_ascii=False), "— à confirmer :", ", ".join(m["statuts_a_couverture_a_confirmer"]) or "aucune")
    base = registry["baseline"]
    if commit_available(base["main"]):
        problems += check_fingerprints(base)
        moved = [p for p, sha in base["modules_sha256"].items() if digest(ROOT / p) != sha]
        print(f"empreintes du baseline conformes au commit {base['main'][:7]} ; modules modifiés depuis : {', '.join(moved) or 'aucun'}")
    else:
        print(f"commit du baseline {base['main']} absent de l'historique local : git fetch origin {base['main']}")
    print("\n".join(problems) if problems else "registre cohérent avec le code courant")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
