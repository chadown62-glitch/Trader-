"""Test de bout en bout du fonctionnement en temps réel (marché simulé, horloge accélérée).

Chaîne vérifiée : relevé de prix → clôture de bougie → caractéristiques →
agent IA → gestionnaire de risque → broker simulé → position avec stop et
objectif → exécution du stop/objectif au fil des prix → trade clôturé et expliqué.
"""

import time

import pytest

from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    clock = Clock()
    src = SyntheticSource(["BTC/USDC", "ETH/USDC"], seed=3, days=215, clock=clock)
    app = App(
        tmp_path_factory.mktemp("flow"),
        source=src,
        start=False,  # pas de thread moteur : on pilote le temps nous-mêmes
        settings_override={
            "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]},
            "market": {"source": "synthetic", "history_days": 210},
            "ai": {"regime_filter": False},
            "risk": {"min_reward_to_fees": 1.5, "cooldown_minutes_after_loss": 0},
        },
    )
    app._startup()
    yield app, clock
    app.db.close()


def test_agent_trades_end_to_end(flow):
    app, clock = flow
    # démarrage de l'IA d'entraînement (préparation exécutée immédiatement, sans thread moteur)
    app.start_agent("ia_entrainement")
    deadline = time.time() + 120
    while "ia_entrainement" not in app.agents and time.time() < deadline:
        time.sleep(0.2)
    assert "ia_entrainement" in app.agents, app.agent_state
    agent = app.agents["ia_entrainement"]
    acc_id = agent.account_id
    opened = closed = 0
    # on fait avancer le temps : relevés toutes les 10 minutes simulées pendant 6 jours
    for _ in range(6 * 24 * 6):
        clock.t += 600
        app.hub.poll()
        opened = app.db.scalar("SELECT COUNT(*) FROM positions WHERE account_id = ? AND origin = 'ai'", (acc_id,), 0)
        closed = app.db.scalar("SELECT COUNT(*) FROM positions WHERE account_id = ? AND origin = 'ai' AND status = 'closed'", (acc_id,), 0)
        if closed >= 2:
            break
    assert opened >= 1, "l'IA aurait dû ouvrir au moins une position en exploration"
    assert closed >= 1, "au moins une position aurait dû être clôturée (stop, objectif ou durée)"
    # chaque décision est expliquée
    taken = app.db.query("SELECT * FROM decisions WHERE kind = 'pris'")
    assert taken and all(len(d["message"]) > 30 for d in taken)
    # chaque trade fermé a un résultat net de frais et un motif
    for p in app.db.query("SELECT * FROM positions WHERE account_id = ? AND status = 'closed'", (acc_id,)):
        assert p["reason_close"] and p["pnl_r"] is not None and p["fees"] > 0
    # aucun solde négatif, aucun ordre orphelin
    bal = app.runtimes[acc_id].broker.balances()
    assert all(v > -1e-9 for v in bal.values())
    open_pos_ids = {p["id"] for p in app.db.query("SELECT id FROM positions WHERE status = 'open'")}
    for o in app.runtimes[acc_id].broker.open_orders():
        assert o["position_id"] in open_pos_ids
    # les situations observées sont enregistrées et se résolvent au fil du temps
    n_cand = app.db.scalar("SELECT COUNT(*) FROM candidates WHERE source = 'live'", (), 0)
    assert n_cand >= opened


def test_stop_agent_keeps_protections(flow):
    app, clock = flow
    app.stop_agent("ia_entrainement")
    assert "ia_entrainement" not in app.agents
    for p in app.db.query("SELECT id FROM positions WHERE status = 'open'"):
        prot = app.db.query("SELECT * FROM orders WHERE position_id = ? AND status = 'open' AND purpose = 'stop'", (p["id"],))
        assert prot, "une position ouverte doit toujours garder son stop-loss"
