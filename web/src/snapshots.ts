import type { FeatureCollection, LineString } from 'geojson';

export type Line = [number, string, string, number, string, [number, number][]];
export interface Geometry { schema_version: number; source: { osm_timestamp?: string; retrieved_at?: string }; lines: Line[] }
export interface GridRecord {
  id: string; utility: string; document: string; member: string; row: number;
  asset_id: string; name: string; voltage_kv: number; published_date: string | null;
  available_mw: number | null; asset_headroom_mw: number | null; upstream_headroom_mw: number | null;
  operational_mw: number | null; forecast_mw: number | null; n1_mw: number | null;
  n1_status: string | null; curtailment: string | null; notes: string | null;
  capacity_scope: string; capacity_status: string;
}
export interface Document { url: string; archive: string; retrieved_at: string; sha256: string }
export interface Utility {
  utility: string; name: string; landing_url: string; status: 'ok' | 'stale' | 'failed';
  error?: string; last_success_at?: string; documents: Record<string, Document>; records: GridRecord[];
}
export interface Snapshot {
  schema_version: number; week: string; created_at: string; geometry: string;
  stats: Record<string, number>; utilities: Record<string, Utility>;
  matches: Record<string, { records: string[]; method: string }>;
}
export interface SnapshotIndex {
  schema_version: number; latest: string;
  snapshots: { week: string; url: string; created_at: string; stats: Record<string, number> }[];
}
export interface LoadedSnapshot { snapshot: Snapshot; geometry: Geometry; records: Map<string, GridRecord>; features: FeatureCollection<LineString> }

export function dataUrl(path: string): string {
  if (!/^(?:index\.json|snapshots\/[\w-]+\.json|geometry\/[a-f0-9]+\.json\.gz|sources\/[a-f0-9]+\.(?:bin|csv|zip))$/.test(path)) {
    throw new Error('Invalid dataset path');
  }
  return new URL(`${import.meta.env.BASE_URL}data/${path}`, window.location.href).href;
}

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(dataUrl(path), { signal, cache: path === 'index.json' ? 'no-cache' : 'default' });
  if (!response.ok) throw new Error(`Cannot load ${path} (HTTP ${response.status})`);
  return response.json() as Promise<T>;
}

export async function loadIndex(): Promise<SnapshotIndex> {
  const index = await fetchJson<SnapshotIndex>('index.json');
  if (index.schema_version !== 1 || !index.snapshots?.length || !index.snapshots.some(s => s.week === index.latest)) {
    throw new Error('No valid weekly snapshots are available');
  }
  return index;
}

const geometryCache = new Map<string, Geometry>();
export async function loadSnapshot(path: string, signal: AbortSignal): Promise<LoadedSnapshot> {
  const snapshot = await fetchJson<Snapshot>(path, signal);
  if (snapshot.schema_version !== 1) throw new Error('Unsupported snapshot version');
  let geometry = geometryCache.get(snapshot.geometry);
  if (!geometry) {
    const response = await fetch(dataUrl(snapshot.geometry), { signal });
    if (!response.ok || !response.body) throw new Error('Cannot load nationwide line geometry');
    const bytes = new Uint8Array(await response.arrayBuffer());
    // Some hosts decompress .gz assets automatically, others serve raw gzip.
    const body = bytes[0] === 0x1f && bytes[1] === 0x8b
      ? new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')))
      : new Response(bytes);
    geometry = await body.json() as Geometry;
    if (geometry.schema_version !== 1 || geometry.lines.length !== snapshot.stats.line_count) throw new Error('Snapshot geometry does not match its metadata');
    geometryCache.set(snapshot.geometry, geometry);
    if (geometryCache.size > 2) geometryCache.delete(geometryCache.keys().next().value!);
  }
  signal.throwIfAborted();
  const records = new Map(Object.values(snapshot.utilities).flatMap(u => u.records.map(r => [r.id, r] as const)));
  const features: FeatureCollection<LineString> = {
    type: 'FeatureCollection',
    features: geometry.lines.map(([id, name, operator, voltage, power, coordinates]) => {
      const match = snapshot.matches[id];
      const record = match?.method === 'operator_name_voltage' ? records.get(match.records[0]) : undefined;
      const stale = record ? snapshot.utilities[record.utility].status !== 'ok' : false;
      return {
        type: 'Feature', id, geometry: { type: 'LineString', coordinates },
        properties: {
          osm_id: id, name, operator, voltage_kv: voltage, power,
          search_name: name.normalize('NFKC').toLowerCase(),
          capacity_status: match?.method === 'candidate' ? 'CANDIDATE' : record?.capacity_status ?? 'UNKNOWN',
          available_mw: record?.available_mw ?? null,
          utility: record?.utility ?? '', stale,
          curtailment: record?.curtailment ?? '',
        },
      };
    }),
  };
  return { snapshot, geometry, records, features };
}
