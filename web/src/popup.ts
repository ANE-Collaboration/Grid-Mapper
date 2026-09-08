import { dataUrl, type GridRecord, type Utility } from './snapshots';
export const STATUS_LABELS: Record<string, string> = {
  AVAILABLE: 'Published capacity > 0 MW',
  NO_PUBLISHED_HEADROOM: 'Published capacity = 0 MW',
  CAPACITY_UNDISCLOSED: 'TSO record · capacity undisclosed',
  CANDIDATE: 'Possible match · unconfirmed',
  UNKNOWN: 'No matched TSO data',
};
export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
const mw = (value: number | null) => value == null ? 'Not published' : value.toLocaleString() + ' MW';
export function recordContent(record: GridRecord, utility: Utility): string {
  const document = utility.documents[record.document];
  return '<article class="record-card"><strong>' + escapeHtml(record.name || '(name withheld)') + '</strong>'
    + '<p>' + escapeHtml(utility.name) + ' · Asset ' + escapeHtml(record.asset_id) + '</p>'
    + (utility.status !== 'ok' ? '<p class="notice warning">Retained from ' + escapeHtml(utility.last_success_at ?? 'a previous collection') + '; this week’s source update failed.</p>' : '')
    + '<dl><dt>Published capacity</dt><dd>' + mw(record.available_mw) + '</dd>'
    + '<dt>Capacity scope</dt><dd>' + (record.capacity_scope === 'upstream' ? 'Includes upstream constraints' : 'Equipment only') + '</dd>'
    + '<dt>Equipment capacity available</dt><dd>' + mw(record.asset_headroom_mw) + '</dd>'
    + '<dt>Operating limit</dt><dd>' + mw(record.operational_mw) + '</dd>'
    + '<dt>Forecast flow</dt><dd>' + mw(record.forecast_mw) + '</dd>'
    + '<dt>Possible curtailment</dt><dd>' + escapeHtml(record.curtailment || 'Not published') + '</dd>'
    + '<dt>N−1 control</dt><dd>' + escapeHtml(record.n1_status || 'Not published') + ' · ' + mw(record.n1_mw) + '</dd>'
    + '<dt>Source publication</dt><dd>' + escapeHtml(record.published_date ?? 'See source document') + '</dd></dl>'
    + (record.notes ? '<p class="record-notes">' + escapeHtml(record.notes) + '</p>' : '')
    + (document ? '<p><a href="' + escapeHtml(document.url) + '" target="_blank" rel="noopener noreferrer">Official download</a> · <a href="' + escapeHtml(dataUrl(document.archive)) + '" download>Archived source</a></p><small>' + escapeHtml(record.member) + ' · CSV row ' + record.row + '</small>' : '')
    + '</article>';
}
