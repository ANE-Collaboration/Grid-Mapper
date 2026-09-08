import type { Map, FilterSpecification } from 'maplibre-gl';
export const NAMED_VOLTAGES = [500, 275, 220, 187, 154, 132, 110, 77, 66, 33, 22];
export const ALL_VOLTAGES = [...NAMED_VOLTAGES, -1, 0];
export const ALL_STATUSES = ['AVAILABLE', 'NO_PUBLISHED_HEADROOM', 'CAPACITY_UNDISCLOSED', 'CANDIDATE', 'UNKNOWN'];
export interface GridFilterState {
  activeVoltages: Set<number>; activeStatuses: Set<string>; minCapacityMw: number; searchQuery: string;
}
export function createInitialFilterState(): GridFilterState {
  return { activeVoltages: new Set(ALL_VOLTAGES), activeStatuses: new Set(ALL_STATUSES), minCapacityMw: 0, searchQuery: '' };
}
export function compileMapLibreFilter(state: GridFilterState): FilterSpecification {
  const voltage = ['to-number', ['get', 'voltage_kv'], 0];
  const selected: unknown[] = ['any', ['in', voltage, ['literal', [...state.activeVoltages].filter(v => v >= 0)]]];
  if (state.activeVoltages.has(-1)) selected.push(['all', ['>', voltage, 0], ['!', ['in', voltage, ['literal', NAMED_VOLTAGES]]]]);
  const clauses: unknown[] = ['all', selected, ['in', ['get', 'capacity_status'], ['literal', [...state.activeStatuses]]]];
  if (state.minCapacityMw > 0) clauses.push(['all', ['!=', ['get', 'available_mw'], null], ['>=', ['get', 'available_mw'], state.minCapacityMw]]);
  if (state.searchQuery.trim()) clauses.push(['in', state.searchQuery.normalize('NFKC').trim().toLowerCase(), ['get', 'search_name']]);
  return clauses as FilterSpecification;
}
export function applyGridFilters(map: Map, state: GridFilterState) {
  const filter = compileMapLibreFilter(state);
  for (const id of ['power-lines-core', 'power-lines-halo']) if (map.getLayer(id)) map.setFilter(id, filter);
}
