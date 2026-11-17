"""MEMECOIN ALPHA ENGINE (mission 56) — détection, veto anti-rug et sélection de memecoins Solana, en SIMULATION.

Aucune transaction n'est construite, signée ni diffusée : les seules lectures passent par le client public verrouillé
du hub (``sentinelle.dmh.net.PublicClient``). Passer le veto ne garantit pas l'absence d'arnaque ; passer le veto et
la sélection ne signifie pas qu'un trade est rentable.
"""
CAN_TRADE = False
PAPER_ONLY = True
NETWORK = "solana"
SOL_MINT = "So11111111111111111111111111111111111111112"
