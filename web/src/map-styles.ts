/**
 * web/src/map-styles.ts
 * Authoritative MapLibre GL JS Paint Expressions, Layer Configurations & Basemap Styles.
 */
import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl';

// ---------------------------------------------------------------------------
// Authoritative Color Palette Constants
// ---------------------------------------------------------------------------
export const AUTHORITATIVE_STATUS_COLORS = {
  AVAILABLE: '#10B981',
  NO_PUBLISHED_HEADROOM: '#EF4444',
  CAPACITY_UNDISCLOSED: '#38BDF8',
  CANDIDATE: '#F59E0B',
  UNKNOWN: '#6B7280',              // Slate Grey
} as const;

export const DARK_HALO_COLOR = '#0B0F19';

export const LAYER_IDS = {
  CORE: 'power-lines-core',
  HALO: 'power-lines-halo',
  HOVER: 'power-lines-hover',
  // Backward compatibility aliases
  TRANSMISSION_LINES: 'transmission_lines',
  TRANSMISSION_LINES_HALO: 'transmission_lines_halo'
} as const;

export const SOURCE_LAYER_NAME = 'transmission_lines';

// ---------------------------------------------------------------------------
// GPU Paint Expressions
// ---------------------------------------------------------------------------

/**
 * Line width scaled by nominal voltage and zoom level.
 * Satisfies:
 *   - width_500 > width_275 > width_66
 *   - width_z12 > width_z4
 *   - max width <= 15.0px
 */
export const coreLineWidthExpression: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  4,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 3.0,
    275, 2.2,
    187, 1.8,
    154, 1.5,
    77, 1.2,
    66, 1.0,
    1.0
  ],
  8,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 5.0,
    275, 3.5,
    187, 2.8,
    154, 2.2,
    77, 1.8,
    66, 1.5,
    1.2
  ],
  12,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 8.0,
    275, 5.0,
    187, 3.8,
    154, 3.0,
    77, 2.4,
    66, 2.0,
    1.5
  ]
];

/**
 * Halo line width: strictly core line width + 2.0px delta across all interpolation stops.
 */
export const haloLineWidthExpression: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  4,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 5.0,
    275, 4.2,
    187, 3.8,
    154, 3.5,
    77, 3.2,
    66, 3.0,
    3.0
  ],
  8,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 7.0,
    275, 5.5,
    187, 4.8,
    154, 4.2,
    77, 3.8,
    66, 3.5,
    3.2
  ],
  12,
  [
    'match',
    ['get', 'voltage_kv'],
    500, 10.0,
    275, 7.0,
    187, 5.8,
    154, 5.0,
    77, 4.4,
    66, 4.0,
    3.5
  ]
];

/**
 * Headroom color expression matching capacity_status.
 */
export const coreLineColorExpression: ExpressionSpecification = [
  'match',
  ['get', 'capacity_status'],
  'AVAILABLE', AUTHORITATIVE_STATUS_COLORS.AVAILABLE,
  'NO_PUBLISHED_HEADROOM', AUTHORITATIVE_STATUS_COLORS.NO_PUBLISHED_HEADROOM,
  'CAPACITY_UNDISCLOSED', AUTHORITATIVE_STATUS_COLORS.CAPACITY_UNDISCLOSED,
  'CANDIDATE', AUTHORITATIVE_STATUS_COLORS.CANDIDATE,
  'UNKNOWN', AUTHORITATIVE_STATUS_COLORS.UNKNOWN,
  AUTHORITATIVE_STATUS_COLORS.UNKNOWN // Fallback for unrecognized/missing status
];

// ---------------------------------------------------------------------------
// Layer Factory Functions
// ---------------------------------------------------------------------------

export function createHaloLayer(sourceId: string, sourceLayer?: string, id: string = LAYER_IDS.HALO): LayerSpecification {
  const layer: any = {
    id,
    type: 'line',
    source: sourceId,
    layout: {
      'line-join': 'round',
      'line-cap': 'round',
      'visibility': 'visible'
    },
    paint: {
      'line-color': DARK_HALO_COLOR,
      'line-width': haloLineWidthExpression,
      'line-opacity': 0.75,
      'line-blur': 0.5
    }
  };
  if (sourceLayer) {
    layer['source-layer'] = sourceLayer;
  }
  return layer;
}

export function createCoreLayer(sourceId: string, sourceLayer?: string, id: string = LAYER_IDS.CORE): LayerSpecification {
  const layer: any = {
    id,
    type: 'line',
    source: sourceId,
    layout: {
      'line-join': 'round',
      'line-cap': 'round',
      'visibility': 'visible'
    },
    paint: {
      'line-color': coreLineColorExpression,
      'line-width': coreLineWidthExpression,
      'line-opacity': 0.85
    }
  };
  if (sourceLayer) {
    layer['source-layer'] = sourceLayer;
  }
  return layer;
}

// ---------------------------------------------------------------------------
// Basemap Configurations
// ---------------------------------------------------------------------------

export interface BasemapConfig {
  id: string;
  name: string;
  style: string | StyleSpecification;
  attribution: string;
}

export const BASEMAP_CONFIGS: Record<string, BasemapConfig> = {
  'dark-matter': {
    id: 'dark-matter',
    name: 'Dark Matter',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    attribution: '© OpenStreetMap contributors, © CARTO'
  },
  'positron': {
    id: 'positron',
    name: 'Positron',
    style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    attribution: '© OpenStreetMap contributors, © CARTO'
  },
  'gsi-ortho': {
    id: 'gsi-ortho',
    name: 'GSI Satellite',
    style: {
      version: 8,
      sources: {
        'gsi-ortho-source': {
          type: 'raster',
          tiles: ['https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg'],
          tileSize: 256,
          attribution: '国土地理院 (GSI)'
        }
      },
      layers: [
        {
          id: 'gsi-ortho-layer',
          type: 'raster',
          source: 'gsi-ortho-source',
          minzoom: 2,
          maxzoom: 18
        }
      ]
    },
    attribution: '国土地理院 (GSI)'
  },
  'gsi-pale': {
    id: 'gsi-pale',
    name: 'GSI Pale',
    style: {
      version: 8,
      sources: {
        'gsi-pale-source': {
          type: 'raster',
          tiles: ['https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '国土地理院 (GSI)'
        }
      },
      layers: [
        {
          id: 'gsi-pale-layer',
          type: 'raster',
          source: 'gsi-pale-source',
          minzoom: 2,
          maxzoom: 18
        }
      ]
    },
    attribution: '国土地理院 (GSI)'
  }
};
