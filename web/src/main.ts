/// <reference types="vite/client" />
import maplibregl, { type GeoJSONSource } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import { BASEMAP_CONFIGS, createCoreLayer, createHaloLayer, LAYER_IDS } from './map-styles';
import { dataUrl, loadIndex, loadSnapshot, type LoadedSnapshot, type SnapshotIndex } from './snapshots';
import { escapeHtml, recordContent, STATUS_LABELS } from './popup';
import { applyGridFilters, createInitialFilterState, ALL_VOLTAGES } from './filters';

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const status = element('dataset-status');
const weekSelect = element<HTMLSelectElement>('snapshot-week');
const utilitySelect = element<HTMLSelectElement>('utility');
export const currentFilterState = createInitialFilterState();
export const map = new maplibregl.Map({
  container: 'map', style: BASEMAP_CONFIGS['dark-matter'].style,
  center: [137.7, 36.8], zoom: 4.6, minZoom: 3, maxZoom: 18,
});
map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
const popup = new maplibregl.Popup({ maxWidth: '390px' });
let index: SnapshotIndex;
let loaded: LoadedSnapshot | undefined;
let controller: AbortController | undefined;
let requestVersion = 0;
let styleReady = false;

function mountData() {
  if (!loaded || !styleReady) return;
  const existing = map.getSource('grid') as GeoJSONSource | undefined;
  if (existing) existing.setData(loaded.features);
  else {
    map.addSource('grid', { type: 'geojson', data: loaded.features, maxzoom: 14, tolerance: 0.2,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>' });
    map.addLayer(createHaloLayer('grid'));
    map.addLayer(createCoreLayer('grid'));
  }
  applyGridFilters(map, currentFilterState);
  element('map-loading').hidden = true;
}
map.on('style.load', () => { styleReady = true; mountData(); });
map.on('error', event => console.warn('Map resource:', event.error.message));

function renderSources() {
  if (!loaded) return;
  const { snapshot } = loaded;
  const stats = snapshot.stats;
  element('total-lines').textContent = stats.line_count.toLocaleString();
  element('matched-lines').textContent = stats.matched_line_count.toLocaleString();
  element('record-count').textContent = stats.record_count.toLocaleString();
  const failures = stats.utilities_stale + stats.utilities_failed;
  status.className = failures ? 'notice warning' : 'notice';
  const old = (Date.now() - Date.parse(snapshot.created_at)) / 86400000 > 10;
  status.textContent = snapshot.week + ' · Collected ' + new Date(snapshot.created_at).toLocaleString()
    + ' · ' + stats.utilities_ok + '/10 utilities updated.'
    + (failures ? ' ' + failures + ' sources need attention.' : '')
    + (old && snapshot.week === index.latest ? ' The latest collection is over 10 days old; check the scheduled workflow.' : '');
  element('geometry-date').textContent = 'OSM geometry: ' + (loaded.geometry.source.osm_timestamp ?? loaded.geometry.source.retrieved_at ?? 'date unavailable')
    + '. Coverage follows OpenStreetMap and is not a complete utility asset register.';
  const selectedUtility = utilitySelect.value;
  utilitySelect.innerHTML = '<option value="">All utilities</option>' + Object.entries(snapshot.utilities)
    .map(([key, utility]) => '<option value="' + escapeHtml(key) + '">' + escapeHtml(utility.name) + '</option>').join('');
  utilitySelect.value = selectedUtility;
  element('sources-list').innerHTML = Object.values(snapshot.utilities).map(utility =>
    '<li><a href="' + escapeHtml(utility.landing_url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(utility.name) + '</a>'
    + '<span class="source-state ' + utility.status + '">' + utility.status + ' · ' + utility.records.length.toLocaleString() + ' records</span>'
    + (utility.status !== 'ok' ? '<small>' + escapeHtml(utility.error) + ' · Last successful collection: ' + escapeHtml(utility.last_success_at ?? 'none') + '</small>' : '') + '</li>').join('');
  element<HTMLAnchorElement>('download-snapshot').href = dataUrl(index.snapshots.find(s => s.week === snapshot.week)!.url);
  renderRecords();
}

async function selectWeek(week: string) {
  const entry = index.snapshots.find(s => s.week === week);
  if (!entry) return;
  controller?.abort();
  controller = new AbortController();
  const version = ++requestVersion;
  popup.remove();
  status.textContent = 'Loading ' + week + '…';
  element('map-loading').hidden = false;
  try {
    const next = await loadSnapshot(entry.url, controller.signal);
    if (version !== requestVersion) return;
    loaded = next;
    renderSources();
    mountData();
    const url = new URL(window.location.href);
    if (week === index.latest) url.searchParams.delete('week');
    else url.searchParams.set('week', week);
    history.replaceState(null, '', url);
  } catch (error) {
    if (version !== requestVersion || (error instanceof DOMException && error.name === 'AbortError')) return;
    status.className = 'notice warning';
    status.textContent = 'Could not load ' + week + ': ' + String(error) + '.' + (loaded ? ' Still displaying ' + loaded.snapshot.week + '.' : ' Reload to retry.');
    weekSelect.value = loaded?.snapshot.week ?? week;
    element('map-loading').hidden = true;
  }
}

function renderRecords() {
  if (!loaded) return;
  const query = element<HTMLInputElement>('record-search').value.normalize('NFKC').trim().toLowerCase();
  const selected = utilitySelect.value;
  const rows = [...loaded.records.values()].filter(record => (!selected || record.utility === selected)
    && (record.name + ' ' + record.asset_id).normalize('NFKC').toLowerCase().includes(query));
  element('record-results-count').textContent = rows.length.toLocaleString() + ' records' + (rows.length > 60 ? ' · showing first 60; narrow your search' : '');
  element('record-results').innerHTML = rows.slice(0, 60).map(record => {
    const utility = loaded!.snapshot.utilities[record.utility];
    return '<details><summary>' + escapeHtml(record.name || '(name withheld)') + ' <span>' + record.voltage_kv + ' kV</span></summary>' + recordContent(record, utility) + '</details>';
  }).join('');
}

weekSelect.addEventListener('change', () => void selectWeek(weekSelect.value));
element<HTMLSelectElement>('basemap').addEventListener('change', event => {
  const id = (event.target as HTMLSelectElement).value;
  popup.remove();
  styleReady = false;
  map.setStyle(BASEMAP_CONFIGS[id].style, { diff: false });
});
element<HTMLInputElement>('search').addEventListener('input', event => {
  currentFilterState.searchQuery = (event.target as HTMLInputElement).value;
  applyGridFilters(map, currentFilterState);
});
element<HTMLInputElement>('min-capacity').addEventListener('input', event => {
  currentFilterState.minCapacityMw = Math.max(0, Number((event.target as HTMLInputElement).value) || 0);
  applyGridFilters(map, currentFilterState);
});
element('voltage-filters').innerHTML = ALL_VOLTAGES.map(voltage => '<label><input type="checkbox" value="' + voltage + '" checked>' + (voltage === -1 ? 'Other' : voltage === 0 ? 'Unknown' : voltage + ' kV') + '</label>').join('');
element('status-filters').innerHTML = Object.entries(STATUS_LABELS).map(([key, label]) => '<label><input type="checkbox" value="' + key + '" checked><i class="dot ' + key + '"></i>' + escapeHtml(label) + '</label>').join('');
for (const id of ['voltage-filters', 'status-filters']) element(id).addEventListener('change', event => {
  const input = event.target as HTMLInputElement;
  if (id === 'voltage-filters') {
    if (input.checked) currentFilterState.activeVoltages.add(Number(input.value));
    else currentFilterState.activeVoltages.delete(Number(input.value));
  } else if (input.checked) currentFilterState.activeStatuses.add(input.value);
  else currentFilterState.activeStatuses.delete(input.value);
  applyGridFilters(map, currentFilterState);
});
element('reset-filters').addEventListener('click', () => {
  Object.assign(currentFilterState, createInitialFilterState());
  element<HTMLInputElement>('search').value = '';
  element<HTMLInputElement>('min-capacity').value = '';
  document.querySelectorAll<HTMLInputElement>('#voltage-filters input, #status-filters input').forEach(input => { input.checked = true; });
  applyGridFilters(map, currentFilterState);
});
element('record-search').addEventListener('input', renderRecords);
utilitySelect.addEventListener('change', renderRecords);
element('toggle-panel').addEventListener('click', () => {
  const closed = document.body.classList.toggle('panel-closed');
  element('toggle-panel').setAttribute('aria-expanded', String(!closed));
});

map.on('click', LAYER_IDS.CORE, event => {
  const feature = event.features?.[0];
  if (!feature || !loaded) return;
  const props = feature.properties;
  const match = loaded.snapshot.matches[props.osm_id];
  const records = match?.records.map(id => loaded!.records.get(id)).filter(r => r != null) ?? [];
  const candidate = match?.method === 'candidate';
  const html = '<h3>' + escapeHtml(props.name || 'Unnamed OSM line') + '</h3><p>' + (props.voltage_kv || 'Unknown') + ' kV · ' + escapeHtml(props.operator || 'Operator not tagged') + '</p>'
    + '<p>' + escapeHtml(STATUS_LABELS[props.capacity_status]) + '</p>'
    + (candidate ? '<p class="notice warning">Possible matches only. Ownership, circuit or section cannot be confirmed; no capacity is assigned to this segment.</p>' : '')
    + records.slice(0, 8).map(record => recordContent(record, loaded!.snapshot.utilities[record.utility])).join('')
    + (records.length > 8 ? '<p>' + (records.length - 8) + ' more candidates. See the published records panel.</p>' : '')
    + (!records.length ? '<p>No verified TSO record is linked to this segment. Its capacity remains unknown.</p>' : '')
    + '<a href="https://www.openstreetmap.org/way/' + Number(props.osm_id) + '" target="_blank" rel="noopener noreferrer">View OSM way ' + Number(props.osm_id) + '</a>';
  popup.setLngLat(event.lngLat).setHTML(html).addTo(map);
});
map.on('mouseenter', LAYER_IDS.CORE, () => { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', LAYER_IDS.CORE, () => { map.getCanvas().style.cursor = ''; });

async function start() {
  try {
    index = await loadIndex();
    weekSelect.innerHTML = index.snapshots.map(s => '<option value="' + escapeHtml(s.week) + '">' + escapeHtml(s.week) + (s.week === index.latest ? ' · Latest' : '') + '</option>').join('');
    weekSelect.disabled = false;
    const requested = new URL(window.location.href).searchParams.get('week');
    weekSelect.value = index.snapshots.some(s => s.week === requested) ? requested! : index.latest;
    await selectWeek(weekSelect.value);
  } catch (error) {
    status.textContent = 'Cannot load the dataset: ' + String(error) + '. Reload to retry.';
    status.className = 'notice warning';
    element('map-loading').hidden = true;
  }
}
void start();
