import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from '@xyflow/react';
import { EmptyState, EmptyStateBody, EmptyStateHeader } from '@patternfly/react-core';
import '@xyflow/react/dist/style.css';

import { useInteractions, useEntities } from '../api/hooks';
import { buildEntityGraph, type EntityNodeData, type InteractionEdgeData } from './entityGraph';

const KIND_BG: Record<string, string> = {
  user: '#4a3c1a',
  external_client: '#3c2a4a',
  agent: '#1a3c4a',
  tool: '#1a4a2a',
  external_service: '#4a1a1a',
  llm: '#2a2a4a',
};

/** Style a plain ReactFlow node with the entity's kind label + palette. */
function decorate(
  nodes: Node<EntityNodeData>[],
  edges: Edge<InteractionEdgeData>[],
): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: nodes.map((n) => {
      const e = n.data.entity;
      return {
        ...n,
        data: { label: `${e.kind}: ${e.display_name}` },
        style: {
          background: KIND_BG[e.kind] ?? '#2c2c2c',
          color: '#e6e6e6',
          border: '1px solid #555',
          borderRadius: 6,
          padding: '6px 10px',
          fontSize: 12,
        },
      };
    }),
    edges: edges.map((e) => ({
      ...e,
      animated: e.data?.error === true,
      style: { stroke: e.data?.error === true ? '#f85149' : '#7fd1ff' },
      labelStyle: { fill: '#aaa', fontSize: 10 },
    })),
  };
}

/**
 * The NEW graph view (ADR-0019): entities as nodes, interactions as directed
 * edges, laid out with dagre. Read-only pan/zoom; error interactions are red +
 * animated. Backed by the same `/interactions` + `/entities` resources the
 * flow tables use.
 */
export function EntityInteractionGraph({ traceId }: { traceId: string }) {
  const { data: entities = [] } = useEntities(traceId);
  const { data: interactions = [] } = useInteractions(traceId);

  const { nodes, edges } = useMemo(() => {
    const graph = buildEntityGraph(entities, interactions);
    return decorate(graph.nodes, graph.edges);
  }, [entities, interactions]);

  if (entities.length === 0) {
    return (
      <EmptyState>
        <EmptyStateHeader titleText="No entities" headingLevel="h4" />
        <EmptyStateBody>
          No entity/interaction graph for this trace yet — the interactions
          processor may still be draining.
        </EmptyStateBody>
      </EmptyState>
    );
  }

  return (
    <div data-testid="entity-graph" style={{ height: 520, background: '#0d0d0d' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#333" gap={16} />
        <Controls showInteractive={false} />
        <MiniMap maskColor="rgba(0,0,0,0.7)" />
      </ReactFlow>
    </div>
  );
}
