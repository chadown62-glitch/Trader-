"""Bounded preparation/check/resolution only. No network, service or model selection."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from sentinelle.services.champion import RegisteredComparison, register_pair
from sentinelle.services.models import ModelStore
from sentinelle.storage.db import Database


class ComparisonAttachment:
    """Optional parallel recorder on an already prepared v2 observer; policy/model remain fixed."""
    def __init__(self, observer, protocol_sha):
        self.observer = observer
        self.comparison = RegisteredComparison(observer.app.db, observer.app.models, protocol_sha, clock=observer.app.market_clock)
        p = self.comparison.protocol
        if (p["models"]["champion"]["sha256"] != observer.protocol["model_sha256"]
                or p["settings"] != observer.settings
                or p["start"] < timestamp(observer.protocol["start"]) or p["end"] > timestamp(observer.protocol["end"])):
            raise ValueError("Le bras de référence doit être exactement le modèle et les réglages v2 figés")
        if observer.agent.shadow_fn is not None:
            raise ValueError("Un observateur parallèle est déjà attaché")
        self.callback = self._record
        observer.agent.shadow_fn = self.callback

    def _record(self, candidate):
        broker = self.observer.agent.broker
        equity, cash = broker.equity()
        self.comparison.record(candidate, self.observer.settings,
                               {"equity_eur": equity, "exposure_eur": max(0., equity-cash),
                                "available_at": self.observer.app.market_clock()})

    def on_bar(self, symbol, features):
        self.observer.on_bar(symbol, features)
        self.comparison.resolve()

    def close(self):
        if self.observer.agent.shadow_fn is self.callback:
            self.observer.agent.shadow_fn = None


def timestamp(value):
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError("Fuseau horaire explicite requis")
    return date.timestamp()


def main():
    c = argparse.ArgumentParser(description=__doc__)
    c.add_argument("action", choices=["import-v2", "register", "check", "resolve"])
    c.add_argument("--data", required=True, type=Path)
    c.add_argument("--champion"); c.add_argument("--challenger")
    c.add_argument("--settings", type=Path); c.add_argument("--start"); c.add_argument("--end")
    c.add_argument("--protocol")
    c.add_argument("--v2-root", type=Path, default=Path("research/prospective-v2"))
    a = c.parse_args()
    if not (a.data / "sentinelle.db").is_file():
        c.error("Installation existante requise ; aucun portefeuille n'est créé")
    db = Database(a.data / "sentinelle.db")
    try:
        models = ModelStore(db, a.data / "models")
        if a.action == "import-v2":
            from tools.prospective import check
            protocol = check(a.v2_root)
            result = models.import_artifact(a.v2_root/"model.joblib", protocol["model_sha256"])
            result.pop("path", None)
        elif a.action == "register":
            if not all((a.champion, a.challenger, a.settings, a.start, a.end)):
                c.error("Versions, réglages et période requis")
            result = register_pair(db, models, champion=a.champion, challenger=a.challenger,
                                   settings=json.loads(a.settings.read_text()), start=timestamp(a.start), end=timestamp(a.end))
        else:
            run = RegisteredComparison(db, models, a.protocol)
            result = {"resolved_now": run.resolve(), "report": run.report()} if a.action == "resolve" else run.report()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
