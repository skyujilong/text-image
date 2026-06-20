import { Handle, Position } from '@xyflow/react'

const FWD_SOURCE_PREFIX = 'source-'
const FWD_TARGET_PREFIX = 'target-'
const BACK_SOURCE = 'back-source'
const BACK_TARGET = 'back-target'

/**
 * 渲染节点的连接点（Handle）：
 * - 前向边：左侧 target-i（入）、右侧 source-i（出），按数量垂直均匀分散，
 *   避免同一节点的多条前向边在端点处重合。
 * - 回边（循环）：底部 back-source（出）、back-target（入），让回边绕底部回环，
 *   与前向边物理分离，方向清晰。
 *
 * Handle 的 id 必须与 useGraphSchema.assignHandles 分配的 id 一一对应。
 * count==1 时居中（与单 handle 旧行为一致）；count==0 时不渲染该方向。
 */
export function renderHandles(
  sourceCount: number,
  targetCount: number,
  hasBackOut: boolean,
  hasBackIn: boolean,
) {
  const handles: React.ReactNode[] = []

  for (let i = 0; i < targetCount; i++) {
    handles.push(
      <Handle
        key={`${FWD_TARGET_PREFIX}${i}`}
        type="target"
        position={Position.Left}
        id={`${FWD_TARGET_PREFIX}${i}`}
        style={targetCount === 1 ? undefined : { top: `${((i + 1) / (targetCount + 1)) * 100}%` }}
      />,
    )
  }
  for (let i = 0; i < sourceCount; i++) {
    handles.push(
      <Handle
        key={`${FWD_SOURCE_PREFIX}${i}`}
        type="source"
        position={Position.Right}
        id={`${FWD_SOURCE_PREFIX}${i}`}
        style={sourceCount === 1 ? undefined : { top: `${((i + 1) / (sourceCount + 1)) * 100}%` }}
      />,
    )
  }
  // 回边出/入 handle 放在底部，左右错开避免重叠
  if (hasBackOut) {
    handles.push(
      <Handle
        key={BACK_SOURCE}
        type="source"
        position={Position.Bottom}
        id={BACK_SOURCE}
        style={{ left: '35%' }}
      />,
    )
  }
  if (hasBackIn) {
    handles.push(
      <Handle
        key={BACK_TARGET}
        type="target"
        position={Position.Bottom}
        id={BACK_TARGET}
        style={{ left: '65%' }}
      />,
    )
  }
  return handles
}
