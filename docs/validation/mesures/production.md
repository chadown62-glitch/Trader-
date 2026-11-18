# Validation de Sentinelle en simulation

Version `0a7305634f786f1747e28a96e4dcb238f77640d1`. Capital initial de 30 €.

| Scénario | Seed | Candidats | AUC | Brier | Trades | Capital final |
|---|---:|---:|---:|---:|---:|---:|
| noise | 41 | 100 | — | — | — | — |
| noise | 41 | 400 | 0.491 | 0.257 | 34 | 29.004 € |
| noise | 41 | 500 | 0.573 | 0.236 | 42 | 29.248 € |
| noise | 41 | 1000 | 0.621 | 0.233 | 30 | 29.743 € |
| noise | 41 | 5000 | — | — | — | — |
| trend | 41 | 100 | — | — | — | — |
| trend | 41 | 400 | 0.504 | 0.293 | 18 | 30.577 € |
| trend | 41 | 500 | 0.514 | 0.302 | 7 | 29.079 € |
| trend | 41 | 1000 | 0.490 | 0.249 | 23 | 31.674 € |
| trend | 41 | 5000 | — | — | — | — |
| mean_reversion | 41 | 100 | — | — | — | — |
| mean_reversion | 41 | 400 | 0.369 | 0.226 | 55 | 29.186 € |
| mean_reversion | 41 | 500 | 0.549 | 0.230 | 52 | 29.276 € |
| mean_reversion | 41 | 1000 | 0.492 | 0.230 | 48 | 29.301 € |
| mean_reversion | 41 | 5000 | — | — | — | — |
| alternating | 41 | 100 | — | — | — | — |
| alternating | 41 | 400 | 0.471 | 0.303 | 18 | 29.844 € |
| alternating | 41 | 500 | 0.541 | 0.310 | 0 | 30.000 € |
| alternating | 41 | 1000 | 0.331 | 0.266 | 17 | 29.936 € |
| alternating | 41 | 5000 | — | — | — | — |
| high_volatility | 41 | 100 | — | — | — | — |
| high_volatility | 41 | 400 | 0.468 | 0.269 | 38 | 30.074 € |
| high_volatility | 41 | 500 | 0.530 | 0.252 | 45 | 31.381 € |
| high_volatility | 41 | 1000 | 0.467 | 0.266 | 35 | 32.879 € |
| high_volatility | 41 | 5000 | — | — | — | — |
| downtrend | 41 | 100 | — | — | — | — |
| downtrend | 41 | 400 | — | — | — | — |
| downtrend | 41 | 500 | — | — | — | — |
| downtrend | 41 | 1000 | — | — | — | — |
| downtrend | 41 | 5000 | — | — | — | — |
| regime_break | 41 | 100 | — | — | — | — |
| regime_break | 41 | 400 | 0.498 | 0.264 | 37 | 30.047 € |
| regime_break | 41 | 500 | 0.599 | 0.227 | 5 | 29.804 € |
| regime_break | 41 | 1000 | 0.530 | 0.228 | 34 | 29.074 € |
| regime_break | 41 | 5000 | — | — | — | — |
| signal_disappears | 41 | 100 | — | — | — | — |
| signal_disappears | 41 | 400 | 0.390 | 0.293 | 29 | 29.137 € |
| signal_disappears | 41 | 500 | 0.615 | 0.270 | 6 | 29.434 € |
| signal_disappears | 41 | 1000 | 0.523 | 0.241 | 33 | 30.293 € |
| signal_disappears | 41 | 5000 | — | — | — | — |
| signal_inverts | 41 | 100 | — | — | — | — |
| signal_inverts | 41 | 400 | 0.565 | 0.281 | 39 | 29.308 € |
| signal_inverts | 41 | 500 | 0.503 | 0.222 | 20 | 29.670 € |
| signal_inverts | 41 | 1000 | 0.480 | 0.223 | 12 | 30.565 € |
| signal_inverts | 41 | 5000 | — | — | — | — |
| spurious_correlation | 41 | 100 | — | — | — | — |
| spurious_correlation | 41 | 400 | 0.390 | 0.293 | 29 | 29.137 € |
| spurious_correlation | 41 | 500 | 0.615 | 0.270 | 6 | 29.434 € |
| spurious_correlation | 41 | 1000 | 0.523 | 0.241 | 33 | 30.293 € |
| spurious_correlation | 41 | 5000 | — | — | — | — |
| delayed_data | 41 | 100 | — | — | — | — |
| delayed_data | 41 | 400 | 0.341 | 0.268 | 19 | 30.226 € |
| delayed_data | 41 | 500 | 0.600 | 0.300 | 0 | 30.000 € |
| delayed_data | 41 | 1000 | 0.417 | 0.260 | 26 | 31.564 € |
| delayed_data | 41 | 5000 | — | — | — | — |
| increasing_noise | 41 | 100 | — | — | — | — |
| increasing_noise | 41 | 400 | 0.441 | 0.271 | 42 | 31.503 € |
| increasing_noise | 41 | 500 | 0.550 | 0.306 | 6 | 30.671 € |
| increasing_noise | 41 | 1000 | 0.548 | 0.255 | 43 | 31.807 € |
| increasing_noise | 41 | 5000 | — | — | — | — |
| high_cost | 41 | 100 | — | — | — | — |
| high_cost | 41 | 400 | 0.407 | 0.259 | 0 | 30.000 € |
| high_cost | 41 | 500 | 0.573 | 0.303 | 0 | 30.000 € |
| high_cost | 41 | 1000 | 0.531 | 0.233 | 0 | 30.000 € |
| high_cost | 41 | 5000 | — | — | — | — |
| low_liquidity | 41 | 100 | — | — | — | — |
| low_liquidity | 41 | 400 | 0.536 | 0.273 | 0 | 30.000 € |
| low_liquidity | 41 | 500 | 0.487 | 0.305 | 0 | 30.000 € |
| low_liquidity | 41 | 1000 | 0.545 | 0.262 | 0 | 30.000 € |
| low_liquidity | 41 | 5000 | — | — | — | — |
| stay_flat | 41 | 100 | — | — | — | — |
| stay_flat | 41 | 400 | — | — | — | — |
| stay_flat | 41 | 500 | — | — | — | — |
| stay_flat | 41 | 1000 | — | — | — | — |
| stay_flat | 41 | 5000 | — | — | — | — |
| noise | 73 | 100 | — | — | — | — |
| noise | 73 | 400 | 0.591 | 0.244 | 0 | 30.000 € |
| noise | 73 | 500 | 0.593 | 0.240 | 0 | 30.000 € |
| noise | 73 | 1000 | 0.537 | 0.250 | 8 | 30.026 € |
| noise | 73 | 5000 | 0.504 | 0.231 | 0 | 30.000 € |
| trend | 73 | 100 | — | — | — | — |
| trend | 73 | 400 | 0.546 | 0.252 | 10 | 29.872 € |
| trend | 73 | 500 | 0.539 | 0.275 | 5 | 30.197 € |
| trend | 73 | 1000 | 0.574 | 0.286 | 14 | 30.682 € |
| trend | 73 | 5000 | 0.633 | 0.256 | 0 | 30.000 € |

## Comparaisons appariées

- noise (41) — AVANTAGE NET NON DÉMONTRÉ
- trend (41) — TESTS INCOMPLETS
- mean_reversion (41) — AVANTAGE NET NON DÉMONTRÉ
- alternating (41) — TESTS INCOMPLETS
- high_volatility (41) — AVANTAGE NET NON DÉMONTRÉ
- downtrend (41) — TESTS INCOMPLETS
- regime_break (41) — AVANTAGE NET NON DÉMONTRÉ
- signal_disappears (41) — AVANTAGE NET NON DÉMONTRÉ
- signal_inverts (41) — TESTS INCOMPLETS
- spurious_correlation (41) — AVANTAGE NET NON DÉMONTRÉ
- delayed_data (41) — TESTS INCOMPLETS
- increasing_noise (41) — AVANTAGE NET NON DÉMONTRÉ
- high_cost (41) — TESTS INCOMPLETS
- low_liquidity (41) — TESTS INCOMPLETS
- stay_flat (41) — TESTS INCOMPLETS
- noise (73) — TESTS INCOMPLETS
- trend (73) — TESTS INCOMPLETS

## Limites

- Le générateur plante un signal dans le volume. Les prix sont entièrement synthétiques.
- Exécution OHLC à l'ouverture suivante avec glissement modélisé. Aucun carnet historique.
- La référence passive choisit son panier par ordre alphabétique avant l’essai, respecte les plafonds initiaux et conserve ses positions.
- Les intervalles sont conditionnels au scénario et à la longueur de bloc. Les jours sans transaction sont inclus.
- Une comparaison synthétique ne promeut aucun modèle dans l'application.
