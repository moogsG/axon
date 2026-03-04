import { MultiDirectedGraph } from 'graphology';
import type { GraphNode, GraphEdge, NodeLabel } from '@/types';

const NODE_COLORS: Record<string, { fill: string; border: string }> = {
  // Core Code → Cool Blues (hue-separated, equal brightness)
  function:   { fill: '#6FA8DC', border: '#8FBCE4' },
  method:     { fill: '#9FC5E8', border: '#B5D4EE' },
  class:      { fill: '#3D85C6', border: '#6FA8DC' },
  // Structure / Architecture → Purple Family
  interface:  { fill: '#8E7CC3', border: '#A99AD0' },
  type_alias: { fill: '#B4A7D6', border: '#C8BDE1' },
  enum:       { fill: '#674EA7', border: '#8E7CC3' },
  // Filesystem → Neutral Family
  file:       { fill: '#7F8C8D', border: '#99A3A4' },
  folder:     { fill: '#566573', border: '#7F8C8D' },
  // Semantic → Warm Accents (rare nodes)
  community:  { fill: '#D4AC0D', border: '#E0C132' },
  process:    { fill: '#48C9B0', border: '#76D7C4' },
};

const DEFAULT_NODE_FILL = '#4a5a6a';
const DEFAULT_NODE_BORDER = '#5a6a7a';
const DEFAULT_EDGE_COLOR = '#2a3a4d';

/** Per-type edge styling: color and Sigma edge program name.
 *  Soft RGBA colors with opacity so edges never overwhelm nodes.
 *  'arrow' = EdgeArrowProgram (directional), 'rectangle' = EdgeRectangleProgram (thick line). */
export const EDGE_STYLES: Record<string, { color: string; program: 'arrow' | 'rectangle' }> = {
  calls:            { color: 'rgba(200,220,255,0.18)', program: 'arrow' },      // soft blue
  imports:          { color: 'rgba(180,255,210,0.18)', program: 'arrow' },      // soft mint
  extends:          { color: 'rgba(255,210,160,0.20)', program: 'arrow' },      // soft orange
  implements:       { color: 'rgba(255,190,210,0.20)', program: 'arrow' },      // soft pink
  uses_type:        { color: 'rgba(220,200,255,0.18)', program: 'arrow' },      // soft violet
  coupled_with:     { color: 'rgba(255,160,160,0.15)', program: 'rectangle' },  // soft red
  member_of:        { color: 'rgba(140,150,170,0.15)', program: 'rectangle' },  // soft gray
  step_in_process:  { color: 'rgba(100,220,200,0.18)', program: 'arrow' },      // soft teal
};

/** Blend a hex color toward its luminance gray by `amount` (0–1). */
function desaturate(hex: string, amount: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const gray = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
  const nr = Math.round(r + (gray - r) * amount);
  const ng = Math.round(g + (gray - g) * amount);
  const nb = Math.round(b + (gray - b) * amount);
  return `#${nr.toString(16).padStart(2, '0')}${ng.toString(16).padStart(2, '0')}${nb.toString(16).padStart(2, '0')}`;
}

export function buildGraphology(nodes: GraphNode[], edges: GraphEdge[]): MultiDirectedGraph {
  const graph = new MultiDirectedGraph();

  for (const node of nodes) {
    const palette = NODE_COLORS[node.label] ?? { fill: DEFAULT_NODE_FILL, border: DEFAULT_NODE_BORDER };
    graph.addNode(node.id, {
      label: node.name,
      x: (Math.random() - 0.5) * 1000,
      y: (Math.random() - 0.5) * 1000,
      size: 3,
      color: palette.fill,
      borderColor: palette.border,
      nodeType: node.label as NodeLabel,
      filePath: node.filePath,
      startLine: node.startLine,
      endLine: node.endLine,
      signature: node.signature,
      language: node.language,
      className: node.className,
      isDead: node.isDead,
      isEntryPoint: node.isEntryPoint,
      isExported: node.isExported,
      directory: node.filePath ? node.filePath.split('/').slice(0, -1).join('/') : '',
    });
  }

  for (const edge of edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) {
      continue;
    }
    try {
      const style = EDGE_STYLES[edge.type] ?? { color: DEFAULT_EDGE_COLOR, program: 'rectangle' as const };
      graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
        edgeType: edge.type,
        type: style.program,
        color: style.color,
        size: 0.6,
        confidence: edge.confidence,
        strength: edge.strength,
        stepNumber: edge.stepNumber,
      });
    } catch {
      // Skip duplicate edge keys
    }
  }

  // --- Visual hierarchy via size only; palette stays intact ---
  // Default state: 15% desaturation (the "resting" look).
  // Hover restores full saturation via nodeReducer.
  graph.forEachNode((id, attrs) => {
    const degree = graph.degree(id);
    const nodeType = attrs.nodeType as string;
    const isClass = nodeType === 'class' || nodeType === 'interface';
    const base = isClass ? 5 : 3;
    const size = base + Math.min(12, Math.log(degree + 1) * 3);
    graph.setNodeAttribute(id, 'size', size);

    // Store original saturated color for hover restoration
    graph.setNodeAttribute(id, '_saturatedColor', attrs.color);
    graph.setNodeAttribute(id, '_saturatedBorder', attrs.borderColor);

    // Apply 15% desaturation as resting state (saturation *= 0.85)
    graph.setNodeAttribute(id, 'color', desaturate(attrs.color as string, 0.15));
    graph.setNodeAttribute(id, 'borderColor', desaturate(attrs.borderColor as string, 0.15));
  });

  return graph;
}
