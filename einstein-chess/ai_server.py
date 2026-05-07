#!/usr/bin/env python3
"""
爱恩斯坦棋 AI WebSocket 中继服务 v5.0
用法: python ai_server.py [--port 8765]

架构 (v5.0 - AI通用池):
  游戏浏览器 <--WS--> ai_server.py <--WS--> 外部AI进程A (ai-abc123)
                              |--WS--> 外部AI进程B (ai-def456)
                              |--WS--> ... (任意数量)

核心变更 (v4→v5):
  - 不再要求 AI 预声明颜色，所有 AI 接入通用池
  - 游戏端通过 game_config 绑定每方使用哪个 ai_id
  - 走棋时按绑定的 ai_id 从池中路由

通过 WebSocket 路径区分客户端类型:
  ws://host:port/game     -> 游戏端（浏览器）
  ws://host:port/ai       -> AI端（通用注册）
"""

import asyncio
import json
import logging
import argparse
import time
import uuid
from typing import Optional, Dict, List, Any

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("请先安装 websockets: pip install websockets")
    exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 内置简单 AI (备用)
# ============================================================

def built_in_ai_move(state: dict) -> dict:
    """内置简单 AI：贪心策略，优先吃子和推进"""
    valid_moves = state.get('valid_moves', [])
    if not valid_moves:
        return {"type": "error", "message": "无可用走法"}

    board = state.get('board', [])
    current_player = state.get('current_player', 'blue')

    best_move = None
    best_score = float('-inf')

    for move in valid_moves:
        score = evaluate_move(board, move, current_player)
        if score > best_score:
            best_score = score
            best_move = move

    if best_move:
        return {
            "type": "move_response",
            "piece_id": best_move.get('piece_id'),
            "from": best_move.get('from'),
            "to": best_move.get('to'),
            "move_index": best_move.get('index', 0)
        }

    import random
    move = random.choice(valid_moves)
    return {
        "type": "move_response",
        "move_index": move.get('index', 0)
    }


def evaluate_move(board: list, move: dict, color: str) -> float:
    """评估走法分数"""
    from_pos = move.get('from', [0, 0])
    to_pos = move.get('to', [0, 0])
    score = 0.0

    tr, tc = to_pos
    if 0 <= tr < 5 and 0 <= tc < 5 and board:
        target = board[tr][tc] if tr < len(board) and tc < len(board[tr]) else None
        if target:
            if target.get('color') != color:
                score += 60
            else:
                score -= 15

    if color == 'blue':
        score += (4 - tr) * 3 + (4 - tc) * 3
        if tr == 0 and tc == 0:
            score += 500
    else:
        score += tr * 3 + tc * 3
        if tr == 4 and tc == 4:
            score += 500

    return score


# ============================================================
# WebSocket 服务器 v5.0 (AI通用池)
# ============================================================

class EWNServer:
    def __init__(self, host: str = 'localhost', port: int = 8765):
        self.host = host
        self.port = port

        # 游戏端连接
        self.game_client: Optional[WebSocketServerProtocol] = None

        # v5.0: AI通用池 — ai_id -> ai_info (不再按颜色预分配槽位)
        # 每个 AI 连接后 register 入池，游戏端按需绑定
        self.ai_clients: Dict[str, dict] = {}  # ai_id -> {ws, name, strategy, connected_at, ...}

        # 每方的后端配置：由游戏端通过 game_config 设置
        # {'red': {'backend': 'external', 'external_ai_id': 'ai-xxx'}, ...}
        self.player_backends: Dict[str, dict] = {
            'red': {'backend': 'human'},
            'blue': {'backend': 'builtin'},
        }
        # 内置AI难度
        self.builtin_difficulty: Dict[str, str] = {'red': 'medium', 'blue': 'medium'}

        # 挂起的请求：request_id -> {data, timestamp, color, resolve_fn}
        self.pending_requests: Dict[str, dict] = {}

        self.stats = {
            'requests': 0,
            'responses': 0,
            'errors': 0,
            'start_time': time.time()
        }

    async def handle_connection(self, websocket: WebSocketServerProtocol):
        """处理新的 WebSocket 连接"""
        path = getattr(websocket, 'path', None)
        if path is None:
            try:
                path = websocket.request.path
            except AttributeError:
                path = '/'

        client_addr = websocket.remote_address
        logger.info(f"新连接: {client_addr} path={path}")

        path_clean = path.strip('/').lower()
        role = None
        if path_clean == 'game':
            role = 'game'
        elif path_clean.startswith('ai'):
            role = 'ai'

        if not role:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"未知路径: {path}，请使用 /game 或 /ai"
            }))
            logger.warning(f"拒绝未知路径连接: {path} from {client_addr}")
            return

        try:
            if role == 'game':
                await self._handle_game_connection(websocket, client_addr)
            else:
                await self._handle_ai_connection(websocket, client_addr)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"连接关闭: {client_addr} ({role}) - {e.code}: {e.reason}")
        except Exception as e:
            logger.error(f"连接处理错误 ({role}): {e}")
        finally:
            if websocket == self.game_client:
                self.game_client = None
                logger.info("[game] 游戏端已断开")
            else:
                self._cleanup_ai_ws(websocket)

    async def _handle_game_connection(self, websocket: WebSocketServerProtocol, addr):
        """处理游戏端连接"""
        if self.game_client and self.game_client != websocket:
            logger.warning("游戏客户端重复连接，替换旧连接")
        self.game_client = websocket
        logger.info(f"[game] 游戏端已连接: {addr}")

        await websocket.send(json.dumps({
            "type": "connected",
            "role": "game",
            "message": "已确认为游戏端",
            "server_version": "5.0.0"
        }))

        # 发送当前AI列表
        await self._send_ai_list_update()

        async for message in websocket:
            await self.handle_game_message(websocket, message)

    async def _handle_ai_connection(self, websocket: WebSocketServerProtocol, addr):
        """处理 AI 端连接 — 等待 register 消息入池"""
        await websocket.send(json.dumps({
            "type": "connected",
            "role": "ai",
            "message": "已确认AI端连接，请发送 register 消息声明身份",
            "server_version": "5.0.0"
        }))

        ai_info = {
            'ws': websocket,
            'addr': addr,
            'ai_id': None,
            'name': None,
            'strategy': None,
            'registered': False,
            'connected_at': time.time(),
        }

        try:
            async for message in websocket:
                await self.handle_ai_message(websocket, message, ai_info)
        finally:
            self._cleanup_ai_by_info(ai_info)

    def _cleanup_ai_ws(self, websocket):
        """根据 WS 对象清理 AI"""
        removed = []
        for ai_id, info in list(self.ai_clients.items()):
            if info.get('ws') == websocket:
                removed.append((ai_id, info.get('name')))
                del self.ai_clients[ai_id]
                break

        for ai_id, name in removed:
            logger.info(f"[ai] {name or ai_id} 已断开")
            asyncio.create_task(self._send_ai_list_update())

    def _cleanup_ai_by_info(self, ai_info):
        """根据 ai_info 清理"""
        ws = ai_info.get('ws')
        ai_id = ai_info.get('ai_id')
        if ai_id and ai_id in self.ai_clients and self.ai_clients[ai_id].get('ws') == ws:
            del self.ai_clients[ai_id]
            logger.info(f"[ai] {ai_info.get('name', ai_id)} 已断开")
            asyncio.create_task(self._send_ai_list_update())

    # ==================== 游戏端消息处理 ====================

    async def handle_game_message(self, websocket: WebSocketServerProtocol, raw):
        """处理游戏端消息"""
        try:
            data = json.loads(raw)
            msg_type = data.get('type', '')

            if msg_type == 'move_request':
                self.stats['requests'] += 1
                await self.handle_move_request(data)

            elif msg_type == 'ping':
                await websocket.send(json.dumps({"type": "pong", "timestamp": time.time()}))

            elif msg_type == 'stats':
                uptime = int(time.time() - self.stats['start_time'])
                await websocket.send(json.dumps({
                    "type": "stats_response",
                    "uptime": uptime,
                    "requests": self.stats['requests'],
                    "responses": self.stats['responses'],
                    "errors": self.stats['errors'],
                    "ai_clients": self.get_ai_list(),
                    "player_backends": {k: dict(v) for k, v in self.player_backends.items()},
                }))

            elif msg_type == 'game_config':
                await self.handle_game_config(data)

            else:
                logger.warning(f"[game] 未知消息类型: {msg_type}")

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误(game): {e}")
            self.stats['errors'] += 1
        except Exception as e:
            logger.error(f"消息处理错误(game): {e}")
            self.stats['errors'] += 1

    async def handle_game_config(self, data: dict):
        """处理游戏端发来的玩家配置 (v5: 支持 external_ai_id 绑定)"""
        config = data.get('config', {})
        for color in ('red', 'blue'):
            if color in config:
                c_cfg = config[color]
                backend = c_cfg.get('backend', 'human')
                self.player_backends[color] = {
                    'backend': backend,
                    'difficulty': c_cfg.get('difficulty'),
                    'external_ai_id': c_cfg.get('external_ai_id'),
                }
                diff = c_cfg.get('difficulty')
                if diff:
                    self.builtin_difficulty[color] = diff

        red_b = self.player_backends.get('red', {}).get('backend', '?')
        blue_b = self.player_backends.get('blue', {}).get('backend', '?')
        red_aid = self.player_backends.get('red', {}).get('external_ai_id', '-')
        blue_aid = self.player_backends.get('blue', {}).get('external_ai_id', '-')
        logger.info(f"玩家配置更新: red={red_b}(aid={red_aid}), blue={blue_b}(aid={blue_aid})")

    async def handle_move_request(self, data: dict):
        """处理走棋请求 — 根据 player_backends 路由到正确的后端"""
        current_player = data.get('current_player', 'blue')

        if current_player not in ('red', 'blue'):
            logger.warning(f"无效的 current_player: {current_player}")
            return

        pconfig = self.player_backends.get(current_player, {})
        backend = pconfig.get('backend', 'human')
        request_id = str(uuid.uuid4())[:8]
        data['_request_id'] = request_id

        if backend == 'external':
            # v5: 按 external_ai_id 从池中查找目标 AI
            target_ai_id = pconfig.get('external_ai_id')
            ai_info = self.ai_clients.get(target_ai_id) if target_ai_id else None

            if ai_info and ai_info.get('ws'):
                self.pending_requests[request_id] = {
                    'data': data,
                    'timestamp': time.time(),
                    'color': current_player,
                    'ai_id': target_ai_id,
                }
                try:
                    await ai_info['ws'].send(json.dumps(data))
                    logger.info(f"转发给{current_player}方外部AI({ai_info['name']}, id={target_ai_id}), reqId: {request_id}")
                    return
                except Exception as e:
                    logger.error(f"转发给AI({target_ai_id})失败: {e}")

            # 外部AI不可用，回退到内置AI
            target_name = ai_info.get('name', target_ai_id) if ai_info else target_ai_id or '?'
            logger.warning(f"{current_player}方外部AI({target_name})不可用，回退到内置AI")
            await self._use_builtin_fallback(data, current_player)

        elif backend == 'builtin':
            await self._use_builtin_fallback(data, current_player)

        else:
            logger.debug(f"{current_player} 配置为 human/backend={backend}，忽略 move_request")

    async def _use_builtin_fallback(self, data: dict, color: str):
        """使用内置AI生成走棋"""
        difficulty = self.builtin_difficulty.get(color, 'medium')
        logger.info(f"使用内置AI({difficulty})生成{color}方走棋...")
        await asyncio.sleep(0.2 + __import__('random').random() * 0.3)
        response = built_in_ai_move(data)
        if self.game_client:
            try:
                await self.game_client.send(json.dumps(response))
                logger.info(f"内置AI({difficulty})走棋: {response}")
                self.stats['responses'] += 1
            except Exception as e:
                logger.error(f"发送内置AI响应失败: {e}")

    # ==================== AI 端消息处理 ====================

    async def handle_ai_message(self, websocket: WebSocketServerProtocol, raw, ai_info):
        """处理 AI 端消息"""
        try:
            data = json.loads(raw)
            msg_type = data.get('type', '')

            if msg_type == 'register':
                await self._handle_ai_register(websocket, data, ai_info)

            elif msg_type == 'move_response':
                self.stats['responses'] += 1
                await self._handle_move_response(data)

            elif msg_type == 'ping':
                await websocket.send(json.dumps({"type": "pong", "timestamp": time.time()}))

            else:
                logger.debug(f"[ai] 未知消息: {msg_type} from {ai_info.get('name', '?')}")

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误(ai): {e}")
            self.stats['errors'] += 1
        except Exception as e:
            logger.error(f"消息处理错误(ai): {e}")
            self.stats['errors'] += 1

    async def _handle_ai_register(self, websocket: WebSocketServerProtocol, data: dict, ai_info):
        """处理 AI 注册请求 (v5: 不再需要颜色，入池即可)"""
        name = data.get('name', f"AI-{uuid.uuid4().hex[:4]}")
        strategy = data.get('strategy', 'unknown')

        # v5: 分配不带颜色的通用 ID
        ai_id = f"ai-{uuid.uuid4().hex[:8]}"

        # 注册入池
        self.ai_clients[ai_id] = {
            'ws': websocket,
            'name': name,
            'strategy': strategy,
            'ai_id': ai_id,
            'connected_at': time.time(),
            'addr': ai_info.get('addr'),
        }

        # 更新 ai_info 引用
        ai_info['ai_id'] = ai_id
        ai_info['name'] = name
        ai_info['strategy'] = strategy
        ai_info['registered'] = True

        await websocket.send(json.dumps({
            "type": "registered",
            "ai_id": ai_id,
            "name": name,
            "message": f"注册成功: {name} (id={ai_id})"
        }))

        logger.info(f"[ai] 注册成功: {name} strategy={strategy} (id={ai_id}) from {ai_info.get('addr')}")

        # 通知游戏端 AI 列表更新
        await self._send_ai_list_update()

    async def _handle_move_response(self, data: dict):
        """将 AI 的走棋决策转发给游戏端"""
        request_id = data.get('_request_id')
        pending = None

        if request_id and request_id in self.pending_requests:
            pending = self.pending_requests.pop(request_id)
        else:
            # 回退：取最早的 pending
            if self.pending_requests:
                _, pending = self.pending_requests.popitem(last=False)

        if self.game_client:
            try:
                # 注入颜色信息供前端识别来源
                if pending:
                    data['_color'] = pending.get('color')
                await self.game_client.send(json.dumps(data))
                ai_name = pending.get('ai_id', '?') if pending else '?'
                logger.info(f"已将AI({ai_name})走棋转发给游戏")
            except Exception as e:
                logger.error(f"转发AI响应失败: {e}")
        else:
            logger.warning("游戏客户端未连接，无法转发AI响应")

    # ==================== 工具方法 ====================

    def get_ai_list(self) -> List[dict]:
        """获取当前在线 AI 列表（v5: 返回池中全部 AI）"""
        result = []
        for ai_id, info in self.ai_clients.items():
            result.append({
                'ai_id': ai_id,
                'name': info.get('name', 'unknown'),
                'strategy': info.get('strategy', 'unknown'),
                'connected_at': info.get('connected_at', 0),
            })
        return result

    async def _send_ai_list_update(self):
        """向游戏端广播 AI 列表更新"""
        if not self.game_client:
            return
        try:
            await self.game_client.send(json.dumps({
                "type": "ai_list_update",
                "ais": self.get_ai_list(),
                "timestamp": time.time()
            }))
        except Exception as e:
            logger.warning(f"发送AI列表更新失败: {e}")

    async def start(self):
        """启动服务器"""
        logger.info("="*60)
        logger.info(f"爱恩斯坦棋 AI 服务 v5.0 (AI通用池)")
        logger.info("="*60)
        logger.info(f"监听地址: ws://{self.host}:{self.port}")
        logger.info("")
        logger.info("  端点:")
        logger.info(f"    游戏端:   ws://{self.host}:{self.port}/game")
        logger.info(f"    AI端(通用): ws://{self.host}:{self.port}/ai")
        logger.info("")
        logger.info("  协议 (v5):")
        logger.info("    AI连接后请发送 register 消息:")
        logger.info('      {"type":"register", "name":"MyBot", "strategy":"greedy"}')
        logger.info("    (不再需要 color 字段，由游戏端选择时动态绑定)")
        logger.info("")
        logger.info("按 Ctrl+C 停止服务")
        logger.info("="*60)

        async with websockets.serve(
            self.handle_connection,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=1024*1024
        ):
            await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description='爱恩斯坦棋 AI WebSocket 服务 v5.0 (AI通用池)')
    parser.add_argument('--host', default='localhost', help='监听地址 (默认: localhost)')
    parser.add_argument('--port', type=int, default=8765, help='监听端口 (默认: 8765)')
    args = parser.parse_args()

    server = EWNServer(host=args.host, port=args.port)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("\n服务已停止")


if __name__ == '__main__':
    main()
