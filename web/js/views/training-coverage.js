export const TrainingCoverage = {
  props: { report: { type: Object, required: true } },
  methods: {
    utc(stamp) { return Number.isFinite(stamp) ? new Date(stamp).toISOString().slice(0, 16).replace('T', ' ') : 'inconnue'; },
  },
  template: `
    <details class="card" style="margin-bottom:14px">
      <summary>Historique, exclusions et sélection des signaux</summary>
      <template v-if="report.data_coverage">
        <p>{{ report.data_coverage.requested_days }} jours demandés · {{ report.data_coverage.source }} · {{ report.data_coverage.timeframe }}. Dates UTC des ouvertures de bougie.</p>
        <div v-for="(a, symbol) in report.data_coverage.assets" :key="symbol">
          <b>{{ symbol }}</b>
          <span v-if="a.excluded"> · {{ a.excluded }}</span>
          <template v-else>
            · {{ a.available }} bougies disponibles du {{ utc(a.first_ms) }} au {{ utc(a.last_ms) }}.
            {{ a.retained }} retenues depuis le {{ utc(a.retained_from_ms) }}.
            <small v-if="a.retained < a.available">La portion antérieure à la dernière interruption de plus de trois jours est exclue.</small>
            <details v-if="a.gaps_ms.length"><summary>{{ a.gaps_ms.length }} interruptions</summary>
              <div v-for="gap in a.gaps_ms" :key="gap[0]">Du {{ utc(gap[0]) }} à la reprise du {{ utc(gap[1]) }}</div>
            </details>
            <div v-if="report.data_coverage.samples_by_asset[symbol]" class="small">
              {{ report.data_coverage.samples_by_asset[symbol].excluded ||
                 (report.data_coverage.samples_by_asset[symbol].eligible + ' situations avec indicateurs disponibles sur ' + report.data_coverage.samples_by_asset[symbol].signals + ' candidates étiquetées') }}
            </div>
          </template>
        </div>
        <p>{{ report.data_coverage.candidates }} situations candidates · {{ report.data_coverage.development_candidates }} admises avant la fenêtre externe · {{ report.n_resolved ?? '—' }} résolues dans l’entraînement.</p>
        <p v-if="report.split_counts">Ajustement {{ report.split_counts.fit }} · calibration {{ report.split_counts.calibration }} · sélection {{ report.split_counts.selection }} · contrôle {{ report.split_counts.holdout }} · exclues {{ report.split_counts.censored }}.</p>
        <p v-if="report.period?.holdout_start">Le contrôle est réservé à cette version. Chevauchements avec les périodes connues d’autres modèles enregistrés : {{ report.data_coverage.internal_holdout_overlaps.join(', ') || 'aucun identifié' }}. <span v-if="report.data_coverage.prior_periods_unknown?.length">Période antérieure non documentée pour {{ report.data_coverage.prior_periods_unknown.join(', ') }}.</span></p>
        <p v-if="report.data_coverage.external_start_ms">Début de la période externe demandée : {{ utc(report.data_coverage.external_start_ms) }} UTC.</p>
        <p v-if="report.signal_filter">{{ report.signal_filter.pass_threshold }} signaux sur {{ report.signal_filter.candidates }} atteignent le seuil {{ report.signal_filter.threshold }} ({{ report.signal_filter.mode }}). Après exclusion des positions qui se chevauchent : {{ report.signal_filter.non_overlapping_model }} avec le filtre et {{ report.signal_filter.non_overlapping_baseline }} pour la référence.</p>
      </template>
      <p v-else>Ce rapport ne conserve pas le détail de couverture par actif.</p>
    </details>`,
};
