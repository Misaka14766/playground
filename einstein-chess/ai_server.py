#!/usr/bin/env python3
"""
爱恩斯坦棋 AI WebSocket 中继服务
用法: python ai_server.py [--port 8765]

此服务作为游戏前端和外部 AI 进程之间的桥接层。
外部 AI 进程连接到此服务，游戏也连接到此服务，
服务负责转发消息。

架构:
  游戏浏览器 <--WS--> ai_server.py <--WS--> 外部AI进程

也可以使用内置简单AI（不需要外部进程）。
"""

import asyncio
import json
import logging
import argparse
import time
from typing import Optional

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
    
    # 随机选择
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
    
    # 检查目标格子
    tr, tc = to_pos
    if 0 <= tr < 5 and 0 <= tc < 5 and board:
        target = board[tr][tc] if tr < len(board) and tc < len(board[tr]) else None
        if target:
            if target.get('color') != color:
                score += 60  # 吃对方子
            else:
                score -= 15  # 吃自己子（不好，但有时有用）
    
    # 前进分数
    if color == 'blue':
        # 蓝方趋向 (0,0)
        score += (4 - tr) * 3 + (4 - tc) * 3
        if tr == 0 and tc == 0:
            score += 500  # 获胜！
    else:
        # 红方趋向 (4,4)
        score += tr * 3 + tc * 3
        if tr == 4 and tc == 4:
            score += 500
    
    return score


# ============================================================
# WebSocket 服务器
# ============================================================

class EWNServer:
    def __init__(self, host: str = 'localhost', port: int = 8765):
        self.host = host
        self.port = port
        self.game_client: Optional[WebSocketServerProtocol] = None
        self.ai_client: Optional[WebSocketServerProtocol] = None
        self.pending_request: Optional[dict] = None
        self.use_builtin_ai = True  # 当没有外部AI时使用内置AI
        self.stats = {
            'requests': 0,
            'responses': 0,
            'errors': 0,
            'start_time': time.time()
        }

    async def handle_connection(self, websocket: WebSocketServerProtocol, path: str = '/'):
        """处理新的 WebSocket 连接"""
        client_addr = websocket.remote_address
        logger.info(f"新连接: {client_addr}, 路径: {path}")
        
        try:
            # 发送欢迎消息和角色分配
            await websocket.send(json.dumps({
                "type": "connected",
                "role": "game" if self.game_client is None else "ai",
                "message": "已连接到爱恩斯坦棋AI服务",
                "server_version": "1.0.0"
            }))
            
            # 角色分配
            if self.game_client is None:
                self.game_client = websocket
                logger.info(f"游戏客户端已连接: {client_addr}")
            elif self.ai_client is None:
                self.ai_client = websocket
                self.use_builtin_ai = False
                logger.info(f"外部AI客户端已连接: {client_addr}")
                # 通知游戏端
                if self.game_client:
                    try:
                        await self.game_client.send(json.dumps({
                            "type": "ai_connected",
                            "message": "外部AI已就绪"
                        }))
                    except:
                        pass
            
            async for message in websocket:
                await self.handle_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"连接关闭: {client_addr} - {e.code}: {e.reason}")
        except Exception as e:
            logger.error(f"连接处理错误: {e}")
        finally:
            if websocket == self.game_client:
                self.game_client = None
                logger.info("游戏客户端已断开")
            elif websocket == self.ai_client:
                self.ai_client = None
                self.use_builtin_ai = True
                logger.info("外部AI客户端已断开，切换到内置AI")

    async def handle_message(self, websocket: WebSocketServerProtocol, raw: str):
        """处理收到的消息"""
        try:
            data = json.loads(raw)
            msg_type = data.get('type', '')
            
            logger.debug(f"收到消息: type={msg_type}")
            
            if msg_type == 'move_request':
                # 游戏请求AI走棋
                self.stats['requests'] += 1
                await self.handle_move_request(data)
                
            elif msg_type == 'move_response':
                # 外部AI返回走棋决策
                self.stats['responses'] += 1
                await self.handle_move_response(data)
                
            elif msg_type == 'ping':
                await websocket.send(json.dumps({"type": "pong", "timestamp": time.time()}))
                
            elif msg_type == 'stats':
                # 请求服务器统计
                uptime = int(time.time() - self.stats['start_time'])
                await websocket.send(json.dumps({
                    "type": "stats_response",
                    "uptime": uptime,
                    "requests": self.stats['requests'],
                    "responses": self.stats['responses'],
                    "errors": self.stats['errors'],
                    "ai_connected": self.ai_client is not None,
                    "game_connected": self.game_client is not None
                }))
                
            else:
                logger.warning(f"未知消息类型: {msg_type}")
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {e}")
            self.stats['errors'] += 1
        except Exception as e:
            logger.error(f"消息处理错误: {e}")
            self.stats['errors'] += 1

    async def handle_move_request(self, data: dict):
        """处理走棋请求"""
        if self.ai_client and not self.use_builtin_ai:
            # 转发给外部AI
            self.pending_request = data
            try:
                await self.ai_client.send(json.dumps(data))
                logger.info(f"已转发请求给外部AI，骰子: {data.get('dice')}, 玩家: {data.get('current_player')}")
            except Exception as e:
                logger.error(f"转发请求失败: {e}")
                # 回退到内置AI
                await self.use_builtin_fallback(data)
        else:
            # 使用内置AI
            await self.use_builtin_fallback(data)

    async def use_builtin_fallback(self, data: dict):
        """使用内置AI生成走棋"""
        logger.info("使用内置AI生成走棋...")
        await asyncio.sleep(0.2 + __import__('random').random() * 0.3)  # 模拟思考时间
        response = built_in_ai_move(data)
        if self.game_client:
            try:
                await self.game_client.send(json.dumps(response))
                logger.info(f"内置AI走棋: {response}")
            except Exception as e:
                logger.error(f"发送内置AI响应失败: {e}")

    async def handle_move_response(self, data: dict):
        """将AI的走棋决策转发给游戏"""
        if self.game_client:
            try:
                await self.game_client.send(json.dumps(data))
                logger.info(f"已将AI走棋转发给游戏: {data}")
            except Exception as e:
                logger.error(f"转发AI响应失败: {e}")
        else:
            logger.warning("游戏客户端未连接，无法转发AI响应")

    async def start(self):
        """启动服务器"""
        logger.info(f"启动爱恩斯坦棋AI服务: ws://{self.host}:{self.port}")
        logger.info("等待连接...")
        logger.info("  - 游戏浏览器打开后会自动连接")
        logger.info("  - 运行 python ai_example.py 连接外部AI")
        logger.info("按 Ctrl+C 停止服务")
        
        async with websockets.serve(
            self.handle_connection,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=1024*1024  # 1MB max message size
        ):
            await asyncio.Future()  # 永远运行


def main():
    parser = argparse.ArgumentParser(description='爱恩斯坦棋 AI WebSocket 服务')
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
