/**
 * Pure layout builder for the entity/interaction graph — the NEW view ADR-0019
 * adds. Entities become nodes, interactions become directed edges (caller →
 * callee), and dagre assigns positions. Kept framework-light (plain Node/Edge
 * objects) so it's Vitest-testable without a DOM; the ReactFlow render in
 * EntityInteractionGraph.tsx consumes the result.
 */
import dagre from 'dagre';
import type { Node, Edge } from '@xyflow/react';
import type { Entity, Interaction } from '../types';

const NODE_WIDTH = 180;
const NODE_HEIGHT = 48;

export interface EntityNodeData extends Record<string, unknown> {
  entity: Entity;
}
export interface InteractionEdgeData extends Record<string, unknown> {
  error: boolean | null;
  summary: string | null;
}

/** Lay out entities as nodes and interactions as edges, positioned by dagre. */
export function buildEntityGraph(
  entities: readonly Entity[],
  interactions: readonly Interaction[],
): { nodes: Node<EntityNodeData>[]; edges: Edge<InteractionEdgeData>[] } {
  const byId = new Map(entities.map((e) => [e.id, e]));

  const nodes: Node<EntityNodeData>[] = entities.map((e) => ({
    id: e.id,
    position: { x: 0, y: 0 },
    data: { entity: e },
    type: 'default',
  }));

  const edges: Edge<InteractionEdgeData>[] = [];
  for (const ix of interactions) {
    // Drop interactions whose endpoints aren't both known entities — the graph
    // can only draw an edge between two nodes it has.
    if (!ix.caller_entity_id || !ix.callee_entity_id) continue;
    if (!byId.has(ix.caller_entity_id) || !byId.has(ix.callee_entity_id)) continue;
    edges.push({
      id: `ix-${ix.id}`,
      source: ix.caller_entity_id,
      target: ix.callee_entity_id,
      label: ix.summary ?? undefined,
      data: { error: ix.error, summary: ix.summary },
    });
  }

  return applyDagreLayout(nodes, edges);
}

/** Top-to-bottom dagre layout, mirroring ui-v2's TopologyGraphView helper. */
function applyDagreLayout<N extends Node, E extends Edge>(
  nodes: N[],
  edges: E[],
): { nodes: N[]; edges: E[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 60 });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }
  dagre.layout(g);

  const laidOut = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });
  return { nodes: laidOut, edges };
}
