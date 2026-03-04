import { useEffect, useRef, useCallback, useState } from 'react';
import Sigma from 'sigma';
import type { MultiDirectedGraph } from 'graphology';
import type { Settings } from 'sigma/settings';
import type { NodeDisplayData, PartialButFor } from 'sigma/types';
import { createNodeBorderProgram } from '@sigma/node-border';
import { EdgeArrowProgram, EdgeRectangleProgram } from 'sigma/rendering';
import FA2LayoutSupervisor from 'graphology-layout-forceatlas2/worker';
import circular from 'graphology-layout/circular';
import circlePack from 'graphology-layout/circlepack';
import { useGraphStore } from '@/stores/graphStore';
import { useGraph } from '@/hooks/useGraph';
import { cn } from '@/lib/utils';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { Minimap } from './Minimap';

/* ── Custom label renderer: dark halo + dynamic font sizing by node importance ── */
function drawNodeLabelWithHalo(
  context: CanvasRenderingContext2D,
  data: PartialButFor<NodeDisplayData, 'x' | 'y' | 'size' | 'label' | 'color'>,
  settings: Settings,
): void {
  if (!data.label) return;
  const font = settings.labelFont;
  const weight = settings.labelWeight;
  const color = settings.labelColor.attribute
    ? (data as Record<string, unknown>)[settings.labelColor.attribute] as string || settings.labelColor.color || '#E6EDF3'
    : settings.labelColor.color;

  // Dynamic font size: larger nodes (higher degree) get bigger labels
  const fontSize = Math.max(10, Math.min(18, 8 + data.size * 0.5));

  context.font = `${weight} ${fontSize}px ${font}`;

  // Strong dark halo for clarity against any background
  context.strokeStyle = '#000000';
  context.lineWidth = 3;
  context.lineJoin = 'round';
  context.globalAlpha = 0.85;
  context.strokeText(data.label, data.x + data.size + 3, data.y + fontSize / 3);

  // Fill on top
  context.fillStyle = color!;
  context.fillText(data.label, data.x + data.size + 3, data.y + fontSize / 3);
  context.globalAlpha = 1;
}

/* ── Custom hover renderer: dark tooltip instead of jarring white ── */
function drawNodeHoverDark(
  context: CanvasRenderingContext2D,
  data: PartialButFor<NodeDisplayData, 'x' | 'y' | 'size' | 'label' | 'color'>,
  settings: Settings,
): void {
  const font = settings.labelFont;
  const weight = settings.labelWeight;
  const size = Math.max(10, Math.min(18, 8 + data.size * 0.5));
  context.font = `${weight} ${size}px ${font}`;

  // Dark background pill
  context.fillStyle = '#1a2030';
  context.shadowOffsetX = 0;
  context.shadowOffsetY = 2;
  context.shadowBlur = 10;
  context.shadowColor = 'rgba(0,0,0,0.6)';

  const PADDING = 3;
  if (typeof data.label === 'string') {
    const textWidth = context.measureText(data.label).width;
    const boxWidth = Math.round(textWidth + 6);
    const boxHeight = Math.round(size + 2 * PADDING);
    const radius = Math.max(data.size, size / 2) + PADDING;
    const angleRadian = Math.asin(boxHeight / 2 / radius);
    const xDeltaCoord = Math.sqrt(Math.abs(radius ** 2 - (boxHeight / 2) ** 2));

    context.beginPath();
    context.moveTo(data.x + xDeltaCoord, data.y + boxHeight / 2);
    context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2);
    context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2);
    context.lineTo(data.x + xDeltaCoord, data.y - boxHeight / 2);
    context.arc(data.x, data.y, radius, angleRadian, -angleRadian);
    context.closePath();
    context.fill();
  } else {
    context.beginPath();
    context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2);
    context.closePath();
    context.fill();
  }

  context.shadowOffsetX = 0;
  context.shadowOffsetY = 0;
  context.shadowBlur = 0;

  // Draw label with halo
  drawNodeLabelWithHalo(context, data, settings);
}

interface GraphCanvasProps {
  className?: string;
}

type PositionMap = Map<string, { x: number; y: number }>;

/**
 * Compute a hierarchical top-to-bottom tree layout.
 *
 * Uses undirected BFS from the highest-degree hub node so the graph
 * fans out into concentric layers of increasing width. This avoids the
 * flat-line bug caused by directed-edge filtering (which made ~80% of
 * nodes roots at layer 0).
 *
 * Disconnected nodes are placed on an extra bottom layer.
 * Within each layer, nodes are sorted by their `directory` attribute to
 * keep related symbols together.
 */
function computeTreeLayout(graph: MultiDirectedGraph): PositionMap {
  const positions: PositionMap = new Map();
  const nodeIds: string[] = [];
  graph.forEachNode((id) => nodeIds.push(id));

  if (nodeIds.length === 0) return positions;

  let hubNode = nodeIds[0];
  let maxDeg = 0;
  for (const id of nodeIds) {
    const deg = graph.degree(id);
    if (deg > maxDeg) {
      maxDeg = deg;
      hubNode = id;
    }
  }

  const layers = new Map<string, number>();
  layers.set(hubNode, 0);
  const queue: string[] = [hubNode];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const depth = layers.get(current)!;

    graph.forEachNeighbor(current, (neighbor) => {
      if (!layers.has(neighbor)) {
        layers.set(neighbor, depth + 1);
        queue.push(neighbor);
      }
    });
  }

  const maxReachable = layers.size > 0 ? Math.max(...layers.values()) : 0;
  for (const id of nodeIds) {
    if (!layers.has(id)) {
      layers.set(id, maxReachable + 1);
    }
  }

  const layerGroups = new Map<number, string[]>();
  for (const [id, depth] of layers) {
    const group = layerGroups.get(depth) ?? [];
    group.push(id);
    layerGroups.set(depth, group);
  }

  for (const [, members] of layerGroups) {
    members.sort((a, b) => {
      const da = (graph.getNodeAttribute(a, 'directory') as string) ?? '';
      const db = (graph.getNodeAttribute(b, 'directory') as string) ?? '';
      return da.localeCompare(db);
    });
  }

  const maxLayer = Math.max(...layerGroups.keys());
  const LAYER_SPACING = 150;

  const widestCount = Math.max(...[...layerGroups.values()].map((g) => g.length));
  const nodeSpacing = Math.max(30, Math.min(80, 2400 / widestCount));

  for (const [depth, members] of layerGroups) {
    const y = depth * LAYER_SPACING;
    const totalWidth = (members.length - 1) * nodeSpacing;
    const startX = -totalWidth / 2;

    for (let i = 0; i < members.length; i++) {
      positions.set(members[i], { x: startX + i * nodeSpacing, y });
    }
  }

  if (maxLayer >= 0) {
    const centerY = (maxLayer * LAYER_SPACING) / 2;
    for (const [id, pos] of positions) {
      positions.set(id, { x: pos.x, y: pos.y - centerY });
    }
  }

  return positions;
}

/**
 * Compute a radial layout with adaptive ring sizing.
 *
 * When a `centerNodeId` is provided (the selected node), it becomes the
 * center -- creating a true ego-graph view. Otherwise falls back to the
 * highest-degree node.
 *
 * Each ring's radius adapts to the number of nodes on it, so outer rings
 * with many nodes expand instead of packing tightly. Rings are offset by
 * half an arc-step to prevent radial stacking.
 */
function computeRadialLayout(graph: MultiDirectedGraph, centerNodeId?: string | null): PositionMap {
  const positions: PositionMap = new Map();
  const nodeIds: string[] = [];
  graph.forEachNode((id) => nodeIds.push(id));

  if (nodeIds.length === 0) return positions;

  let centerNode: string;
  if (centerNodeId && graph.hasNode(centerNodeId)) {
    centerNode = centerNodeId;
  } else {
    centerNode = nodeIds[0];
    let maxDegree = 0;
    for (const id of nodeIds) {
      const deg = graph.degree(id);
      if (deg > maxDegree) {
        maxDegree = deg;
        centerNode = id;
      }
    }
  }

  const ringMap = new Map<string, number>();
  ringMap.set(centerNode, 0);
  const queue: string[] = [centerNode];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentRing = ringMap.get(current)!;

    graph.forEachNeighbor(current, (neighbor) => {
      if (!ringMap.has(neighbor)) {
        ringMap.set(neighbor, currentRing + 1);
        queue.push(neighbor);
      }
    });
  }

  const ringGroups = new Map<number, string[]>();
  let maxRing = 0;
  for (const [id, ring] of ringMap) {
    const group = ringGroups.get(ring) ?? [];
    group.push(id);
    ringGroups.set(ring, group);
    if (ring > maxRing) maxRing = ring;
  }

  const orphans = nodeIds.filter((id) => !ringMap.has(id));
  if (orphans.length > 0) {
    ringGroups.set(maxRing + 1, orphans);
  }

  positions.set(centerNode, { x: 0, y: 0 });

  let prevRadius = 0;
  const sortedRings = [...ringGroups.keys()].filter((r) => r > 0).sort((a, b) => a - b);

  for (const ring of sortedRings) {
    const members = ringGroups.get(ring)!;
    const count = members.length;

    // Radius must be large enough to fit `count` nodes without overlap.
    const circumferenceNeeded = count * 80;
    const radiusFromCount = circumferenceNeeded / (2 * Math.PI);
    const radius = Math.max(prevRadius + 150, radiusFromCount);
    prevRadius = radius;

    // Offset each ring by half an arc-step to prevent radial stacking.
    const arcStep = (2 * Math.PI) / count;
    const ringOffset = (ring % 2) * (arcStep / 2);

    for (let i = 0; i < count; i++) {
      const angle = ringOffset + arcStep * i;
      positions.set(members[i], {
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      });
    }
  }

  return positions;
}

/**
 * Animate node positions from their current locations to new target positions
 * over a given duration using requestAnimationFrame with ease-out cubic.
 *
 * Writes the current frame ID to `frameRef` on every tick so that
 * `cancelAnimationFrame(frameRef.current)` always cancels the latest
 * scheduled frame -- fixing rapid layout-switch glitches.
 */
function animatePositions(
  graph: MultiDirectedGraph,
  targets: PositionMap,
  duration: number,
  frameRef: React.MutableRefObject<number>,
  onComplete?: () => void,
): void {
  const starts: PositionMap = new Map();
  graph.forEachNode((id, attrs) => {
    starts.set(id, { x: attrs.x as number, y: attrs.y as number });
  });

  const t0 = performance.now();

  function tick() {
    const elapsed = performance.now() - t0;
    const progress = Math.min(elapsed / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3);

    graph.forEachNode((id) => {
      const start = starts.get(id);
      const target = targets.get(id);
      if (!start || !target) return;

      graph.setNodeAttribute(id, 'x', start.x + (target.x - start.x) * ease);
      graph.setNodeAttribute(id, 'y', start.y + (target.y - start.y) * ease);
    });

    if (progress < 1) {
      frameRef.current = requestAnimationFrame(tick);
    } else {
      onComplete?.();
    }
  }

  frameRef.current = requestAnimationFrame(tick);
}

export function GraphCanvas({ className }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const layoutRef = useRef<FA2LayoutSupervisor | null>(null);
  const animFrameRef = useRef<number>(0);
  const { graphRef, loading, error } = useGraph();
  const [layoutRunning, setLayoutRunning] = useState(false);
  const [sigmaReady, setSigmaReady] = useState(false);

  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const hoveredNodeId = useGraphStore((s) => s.hoveredNodeId);
  const highlightedNodeIds = useGraphStore((s) => s.highlightedNodeIds);
  const visibleNodeTypes = useGraphStore((s) => s.visibleNodeTypes);
  const visibleEdgeTypes = useGraphStore((s) => s.visibleEdgeTypes);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHoveredNode = useGraphStore((s) => s.setHoveredNode);
  const layoutMode = useGraphStore((s) => s.layoutMode);
  const minimapVisible = useGraphStore((s) => s.minimapVisible);

  const zoomIn = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedZoom({ duration: 200 });
    }
  }, []);

  const zoomOut = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedUnzoom({ duration: 200 });
    }
  }, []);

  const fitToScreen = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedReset({ duration: 300 });
    }
  }, []);

  const toggleLayout = useCallback(() => {
    const graph = graphRef.current;
    const layout = layoutRef.current;
    if (!layout || !graph) return;

    if (layout.isRunning()) {
      layout.stop();
      setLayoutRunning(false);
    } else {
      // Kill the stale supervisor and create a fresh one. This resets FA2's
      // internal speed accumulator, preventing vibration on resume after the
      // layout has already converged. Settings match initial run but with
      // higher slowDown for gentler refinement.
      layout.kill();
      const fresh = new FA2LayoutSupervisor(graph, {
        settings: {
          gravity: 1,
          scalingRatio: 5,
          strongGravityMode: true,
          linLogMode: false,
          outboundAttractionDistribution: true,
          barnesHutOptimize: true,
          barnesHutTheta: 0.5,
          slowDown: 20,
        },
      });
      fresh.start();
      layoutRef.current = fresh;
      setLayoutRunning(true);

      setTimeout(() => {
        if (fresh.isRunning()) {
          fresh.stop();
          setLayoutRunning(false);
        }
      }, 6_000);
    }
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !graphRef.current) return;
    const graph = graphRef.current;

    // Snapshot the current store values for the reducers. The refresh effect
    // below triggers sigma.refresh() whenever these change, which causes
    // Sigma to re-invoke the reducers with fresh closure values.
    const BorderedNodeProgram = createNodeBorderProgram({
      borders: [
        { size: { value: 0.06, mode: 'relative' }, color: { attribute: '_outlineColor', defaultValue: '#0a0a0a' } },
        { size: { value: 0.14, mode: 'relative' }, color: { attribute: 'borderColor', defaultValue: '#5a6a7a' } },
        { size: { fill: true }, color: { attribute: 'color' } },
      ],
    });

    const sigma = new Sigma(graph, container, {
      renderLabels: true,
      labelFont: 'JetBrains Mono, monospace',
      labelSize: 12,
      labelWeight: '500',
      labelColor: { color: '#E6EDF3' },
      defaultEdgeColor: '#2a3a4d',
      defaultNodeColor: '#4a5a6a',
      labelRenderedSizeThreshold: 12,
      labelDensity: 0.15,
      labelGridCellSize: 200,
      defaultDrawNodeLabel: drawNodeLabelWithHalo,
      defaultDrawNodeHover: drawNodeHoverDark,
      zoomToSizeRatioFunction: (r: number) => Math.max(r, 0.15),
      hideEdgesOnMove: true,
      defaultNodeType: 'bordered',
      nodeProgramClasses: {
        bordered: BorderedNodeProgram,
      },
      defaultEdgeType: 'rectangle',
      edgeProgramClasses: {
        arrow: EdgeArrowProgram,
        rectangle: EdgeRectangleProgram,
      },

      nodeReducer: (node, data) => {
        const res = { ...data };
        const nodeType = (data.nodeType ?? '') as string;
        const state = useGraphStore.getState();

        if (!state.visibleNodeTypes.has(nodeType)) {
          res.hidden = true;
          return res;
        }

        // Set-based highlighting (file/folder/community/dead code).
        if (state.highlightedNodeIds.size > 0) {
          if (state.highlightedNodeIds.has(node)) {
            res.size = (res.size ?? 3) * 1.3;
            res.zIndex = 2;
          } else {
            res.color = '#141a22';
            res.borderColor = '#141a22';
            res.label = '';
            res.zIndex = 0;
          }
          if (state.hoveredNodeId && node === state.hoveredNodeId) {
            res.highlighted = true;
            res.forceLabel = true;
          }
          return res;
        }

        if (state.selectedNodeId && node !== state.selectedNodeId) {
          const isNeighbor =
            graph.hasEdge(state.selectedNodeId, node) ||
            graph.hasEdge(node, state.selectedNodeId);
          if (!isNeighbor) {
            res.color = '#141a22';
            res.borderColor = '#141a22';
            res.label = '';
            res.zIndex = 0;
          } else {
            res.forceLabel = true;
            res.zIndex = 2;
          }
        }

        if (state.selectedNodeId && node === state.selectedNodeId) {
          res.highlighted = true;
          res.forceLabel = true;
          res.zIndex = 3;
        }

        // Focus mode: hovering restores full saturation + fades non-neighbors
        if (state.hoveredNodeId && !state.selectedNodeId) {
          if (node === state.hoveredNodeId) {
            // Restore full saturation on hovered node — only this node gets forceLabel
            res.color = (data as Record<string, unknown>)._saturatedColor as string || res.color;
            res.borderColor = (data as Record<string, unknown>)._saturatedBorder as string || res.borderColor;
            res.highlighted = true;
            res.forceLabel = true;
            res.zIndex = 3;
          } else {
            const isNeighbor =
              graph.hasEdge(state.hoveredNodeId, node) ||
              graph.hasEdge(node, state.hoveredNodeId);
            if (isNeighbor) {
              // Neighbors get full saturation but NO forceLabel (prevents label pile-up)
              res.color = (data as Record<string, unknown>)._saturatedColor as string || res.color;
              res.borderColor = (data as Record<string, unknown>)._saturatedBorder as string || res.borderColor;
              res.zIndex = 2;
            } else {
              res.color = '#1a2030';
              res.borderColor = '#1a2030';
              res.label = '';
              res.zIndex = 0;
            }
          }
        } else if (state.hoveredNodeId && node === state.hoveredNodeId) {
          res.color = (data as Record<string, unknown>)._saturatedColor as string || res.color;
          res.borderColor = (data as Record<string, unknown>)._saturatedBorder as string || res.borderColor;
          res.highlighted = true;
          res.forceLabel = true;
        }

        return res;
      },

      edgeReducer: (edge, data) => {
        const res = { ...data };
        const edgeType = (data.edgeType ?? '') as string;
        const state = useGraphStore.getState();

        if (!state.visibleEdgeTypes.has(edgeType)) {
          res.hidden = true;
          return res;
        }

        if (state.highlightedNodeIds.size > 0) {
          const source = graph.source(edge);
          const target = graph.target(edge);
          if (state.highlightedNodeIds.has(source) && state.highlightedNodeIds.has(target)) {
            // Keep per-type color, just boost size for emphasis
            res.size = 1.0;
          } else {
            res.hidden = true;
          }
          return res;
        }

        if (state.selectedNodeId) {
          const source = graph.source(edge);
          const target = graph.target(edge);
          if (source !== state.selectedNodeId && target !== state.selectedNodeId) {
            res.hidden = true;
          } else {
            // Keep per-type color, just boost size for emphasis
            res.size = 1.2;
          }
        }

        // Focus mode: hovering fades non-neighbor edges when no selection active
        if (state.hoveredNodeId && !state.selectedNodeId) {
          const source = graph.source(edge);
          const target = graph.target(edge);
          if (source !== state.hoveredNodeId && target !== state.hoveredNodeId) {
            res.hidden = true;
          } else {
            res.size = 1.2;
          }
        }

        return res;
      },
    });

    sigmaRef.current = sigma;
    setSigmaReady(true);

    let draggedNode: string | null = null;
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    const DRAG_THRESHOLD = 5;

    sigma.on('downNode', (e) => {
      isDragging = true;
      draggedNode = e.node;
      graph.setNodeAttribute(draggedNode, 'fixed', true);
      if (layoutRef.current?.isRunning()) layoutRef.current.stop();
      sigma.getCamera().disable();
    });

    sigma.getMouseCaptor().on('mousemovebody', (e) => {
      if (!isDragging || !draggedNode) return;
      const pos = sigma.viewportToGraph(e);
      graph.setNodeAttribute(draggedNode, 'x', pos.x);
      graph.setNodeAttribute(draggedNode, 'y', pos.y);
      e.preventSigmaDefault();
      e.original.preventDefault();
      e.original.stopPropagation();
    });

    sigma.getMouseCaptor().on('mousedown', (e) => {
      dragStartX = e.x;
      dragStartY = e.y;
    });

    sigma.getMouseCaptor().on('mouseup', (e) => {
      if (draggedNode) {
        const dx = Math.abs(e.x - dragStartX);
        const dy = Math.abs(e.y - dragStartY);
        if (dx < DRAG_THRESHOLD && dy < DRAG_THRESHOLD) {
          selectNode(draggedNode);
        }
        graph.removeNodeAttribute(draggedNode, 'fixed');
      }
      isDragging = false;
      draggedNode = null;
      sigma.getCamera().enable();
    });

    sigma.on('clickStage', () => {
      selectNode(null);
      useGraphStore.getState().setHighlightedNodes(new Set());
    });

    sigma.on('enterNode', ({ node }) => {
      setHoveredNode(node);
      container.style.cursor = 'grab';
    });

    sigma.on('leaveNode', () => {
      setHoveredNode(null);
      container.style.cursor = 'default';
    });

    const layout = new FA2LayoutSupervisor(graph, {
      settings: {
        gravity: 1,
        scalingRatio: 5,
        strongGravityMode: true,
        linLogMode: false,
        outboundAttractionDistribution: true,
        barnesHutOptimize: true,
        barnesHutTheta: 0.5,
        slowDown: 10,
      },
    });
    layout.start();
    layoutRef.current = layout;
    setLayoutRunning(true);

    const timer = setTimeout(() => {
      if (layout.isRunning()) {
        layout.stop();
        setLayoutRunning(false);
      }
    }, 6_000);

    return () => {
      clearTimeout(timer);
      cancelAnimationFrame(animFrameRef.current);
      layout.kill();
      sigma.kill();
      sigmaRef.current = null;
      layoutRef.current = null;
      setSigmaReady(false);
      setLayoutRunning(false);
    };
  }, [loading, selectNode, setHoveredNode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const graph = graphRef.current;
    const layout = layoutRef.current;
    if (!graph || !sigmaRef.current) return;

    cancelAnimationFrame(animFrameRef.current);

    if (layoutMode === 'force') {
      if (layout && !layout.isRunning()) {
        layout.start();
        setLayoutRunning(true);

        const timer = setTimeout(() => {
          if (layout.isRunning()) {
            layout.stop();
            setLayoutRunning(false);
          }
        }, 6_000);

        return () => clearTimeout(timer);
      }
    } else {
      if (layout && layout.isRunning()) {
        layout.stop();
        setLayoutRunning(false);
      }

      let targets: PositionMap;

      if (layoutMode === 'tree') {
        targets = computeTreeLayout(graph);
      } else if (layoutMode === 'radial') {
        targets = computeRadialLayout(graph, selectedNodeId);
      } else if (layoutMode === 'community') {
        const communities = useGraphStore.getState().communities;
        const memberToCommunity = new Map<string, string>();
        for (const c of communities) {
          for (const memberId of c.members) {
            memberToCommunity.set(memberId, c.id);
          }
        }

        graph.forEachNode((id, attrs) => {
          const communityId = memberToCommunity.get(id) ?? (attrs.directory as string) ?? 'unknown';
          graph.setNodeAttribute(id, 'community', communityId);
        });

        circlePack.assign(graph, { hierarchyAttributes: ['community'], scale: 1000 });

        targets = new Map();
        graph.forEachNode((id, attrs) => {
          targets.set(id, { x: attrs.x as number, y: attrs.y as number });
        });
      } else {
        // 'circular' layout -- scale adapts to node count so nodes don't bunch up.
        const nodeCount = graph.order;
        const circularScale = Math.max(500, nodeCount * 2);
        circular.assign(graph, { scale: circularScale });

        targets = new Map();
        graph.forEachNode((id, attrs) => {
          targets.set(id, { x: attrs.x as number, y: attrs.y as number });
        });
      }

      animatePositions(graph, targets, 500, animFrameRef);
    }
  }, [layoutMode, selectedNodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    sigmaRef.current?.refresh();
  }, [selectedNodeId, hoveredNodeId, highlightedNodeIds, visibleNodeTypes, visibleEdgeTypes]);

  const nodes = useGraphStore((s) => s.nodes);
  const graphEmpty = !loading && !error && nodes.length === 0;

  if (error) {
    return (
      <div
        className={cn('flex items-center justify-center h-full', className)}
        style={{
          color: 'var(--danger)',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
        }}
      >
        Failed to load graph: {error}
      </div>
    );
  }

  if (loading) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <LoadingSpinner message="Loading graph..." />
      </div>
    );
  }

  if (graphEmpty) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <EmptyState message="No graph data. Run `axon index` first." />
      </div>
    );
  }

  return (
    <div
      className={cn('relative w-full h-full', className)}
      style={{ background: 'radial-gradient(ellipse at 50% 50%, #0F1620 0%, #0A0E14 70%)' }}
    >
      <div ref={containerRef} className="w-full h-full" />
      <GraphControls
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFitToScreen={fitToScreen}
        onToggleLayout={toggleLayout}
        layoutRunning={layoutRunning}
      />
      {layoutRunning && <LayoutIndicator />}
      {minimapVisible && sigmaReady && sigmaRef.current && (
        <Minimap sigma={sigmaRef.current} />
      )}
    </div>
  );
}

interface GraphControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitToScreen: () => void;
  onToggleLayout: () => void;
  layoutRunning: boolean;
}

function GraphControls({
  onZoomIn,
  onZoomOut,
  onFitToScreen,
  onToggleLayout,
  layoutRunning,
}: GraphControlsProps) {
  return (
    <div
      className="absolute bottom-3 left-3 flex flex-col gap-1"
      style={{ zIndex: 10 }}
    >
      <ControlButton onClick={onZoomIn} title="Zoom in" aria-label="Zoom in">
        <PlusIcon />
      </ControlButton>
      <ControlButton onClick={onZoomOut} title="Zoom out" aria-label="Zoom out">
        <MinusIcon />
      </ControlButton>
      <ControlButton onClick={onFitToScreen} title="Fit to screen" aria-label="Fit to screen">
        <MaximizeIcon />
      </ControlButton>
      <ControlButton onClick={onToggleLayout} title={layoutRunning ? 'Pause layout' : 'Resume layout'} aria-label={layoutRunning ? 'Pause layout' : 'Resume layout'}>
        {layoutRunning ? <PauseIcon /> : <PlayIcon />}
      </ControlButton>
    </div>
  );
}

interface ControlButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
}

function ControlButton({ children, ...props }: ControlButtonProps) {
  return (
    <button
      type="button"
      className="flex items-center justify-center transition-colors"
      style={{
        width: 24,
        height: 24,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 2,
        color: 'var(--text-secondary)',
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
      }}
      {...props}
    >
      {children}
    </button>
  );
}

function PlusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <line x1="6" y1="2" x2="6" y2="10" />
      <line x1="2" y1="6" x2="10" y2="6" />
    </svg>
  );
}

function MinusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <line x1="2" y1="6" x2="10" y2="6" />
    </svg>
  );
}

function MaximizeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="2" width="8" height="8" rx="0.5" />
      <line x1="4" y1="4" x2="4" y2="4.01" strokeLinecap="round" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" stroke="none">
      <polygon points="3,1.5 10,6 3,10.5" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" stroke="none">
      <rect x="2.5" y="2" width="2.5" height="8" rx="0.5" />
      <rect x="7" y="2" width="2.5" height="8" rx="0.5" />
    </svg>
  );
}

function LayoutIndicator() {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 120,
        left: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 2,
        padding: '3px 8px',
        zIndex: 10,
      }}
    >
      <span
        style={{
          display: 'inline-block',
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: 'var(--accent)',
          animation: 'axon-pulse 1.4s ease-in-out infinite',
        }}
      />
      <span
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10,
          color: 'var(--text-secondary)',
        }}
      >
        Optimizing layout...
      </span>
      <style>{`
        @keyframes axon-pulse {
          0%, 100% { transform: scale(0.8); opacity: 0.5; }
          50%      { transform: scale(1.2); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
