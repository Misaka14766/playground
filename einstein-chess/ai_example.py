#!/usr/bin/env python3
"""
爱恩斯坦棋 外部AI接入示例
===============================
此文件演示如何编写一个外部 AI 进程接入爱恩斯坦棋游戏。

使用方法:
  1. 先启动服务: python ai_server.py
  2. 在浏览器中打开游戏，选择「外部AI」模式
  3. 运行此文件: python ai_example.py

协议说明:
  - 连接到 ws://localhost:8765/ai
  - 接收 move_request 消息，解析棋盘和合法走法
  - 返回 move_response 消息，指定要走的棋步

您可以修改 MyAI 类中的 choose_move 方法来实现自己的AI策略。
"""

import asyncio
import json
import random
import logging
import sys
import time
from typing import List, Dict, Any, Tuple

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [AI] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 类型定义
# ============================================================
Board = List[List[Optional[Dict]]]
Move = Dict[str, Any]
GameState = Dict[str, Any]


# ============================================================
# 棋盘工具函数
# ============================================================

def find_pieces(board: Board, color: str) -> List[Tuple[int, int, int]]:
    """找到指定颜色的所有棋子，返回 [(row, col, id), ...]"""
    pieces = []
    for r in range(5):
        for c in range(5):
            cell = board[r][c]
            if cell and cell.get('color') == color:
                pieces.append((r, c, cell['id']))
    return pieces


def simulate_move(board: Board, move: Move) -> Board:
    """模拟一步走法，返回新棋盘"""
    new_board = [row[:] for row in board]
    fr, fc = move['from']
    tr, tc = move['to']
    new_board[tr][tc] = new_board[fr][fc]
    new_board[fr][fc] = None
    return new_board


def get_valid_moves(board: Board, color: str, dice: int) -> List[Move]:
    """根据骰子点数获取合法走法"""
    pieces = find_pieces(board, color)
    piece_ids = [p[2] for p in pieces]
    
    # 找到要走的棋子 ID
    movable_ids = []
    if dice in piece_ids:
        movable_ids = [dice]
    else:
        lower = max((id for id in piece_ids if id < dice), default=None)
        upper = min((id for id in piece_ids if id > dice), default=None)
        if lower is not None:
            movable_ids.append(lower)
        if upper is not None:
            movable_ids.append(upper)
    
    # 获取这些棋子的走法
    moves = []
    dirs = [(0, 1), (1, 0), (1, 1)] if color == 'red' else [(0, -1), (-1, 0), (-1, -1)]
    
    for r, c, piece_id in pieces:
        if piece_id not in movable_ids:
            continue
        for dr, dc in dirs:
            nr, nc = r + dr, c + dc
            if 0 <= nr < 5 and 0 <= nc < 5:
                moves.append({
                    'piece_id': piece_id,
                    'from': [r, c],
                    'to': [nr, nc]
                })
    
    return moves


def check_win(board: Board) -> Optional[str]:
    """检查是否有人获胜，返回获胜方颜色或 None"""
    red_pieces = find_pieces(board, 'red')
    blue_pieces = find_pieces(board, 'blue')
    
    if not blue_pieces:
        return 'red'
    if not red_pieces:
        return 'blue'
    
    for r, c, _ in red_pieces:
        if r == 4 and c == 4:
            return 'red'
    for r, c, _ in blue_pieces:
        if r == 0 and c == 0:
            return 'blue'
    
    return None


def board_to_str(board: Board) -> str:
    """将棋盘转换为可读字符串"""
    rows = []
    for r in range(5):
        row = []
        for c in range(5):
            cell = board[r][c]
            if cell is None:
                row.append(' . ')
            elif cell['color'] == 'red':
                row.append(f'R{cell["id"]} ')
            else:
                row.append(f'B{cell["id"]} ')
        rows.append(''.join(row))
    return '\n'.join(f'  {r}  |{"".join(row)}' for r, row in enumerate(rows))


# ============================================================
# AI 策略实现
# ============================================================

class MyAI:
    """
    示例 AI 实现

    您可以修改 choose_move 方法来实现自己的策略。
    目前实现了两种策略：
    - random_move: 随机选择
    - greedy_move: 贪心策略（优先吃子和前进）

    切换策略：使用 --strategy 参数或修改 choose_move 中的调用
    """

    def __init__(self, color: str = 'blue', strategy: str = 'greedy'):
        self.color = color
        self.strategy = strategy
        self.move_count = 0
        self.time_limit = 240       # 每方总时间（秒），由 game_start 设置
        self.time_remaining = 240   # 当前方剩余时间（秒），由 move_request 更新
        logger.info(f"AI 初始化: 颜色={color}, 策略={strategy}")

    def choose_move(self, state: GameState, valid_moves: List[Move]) -> Move:
        """
        核心决策函数 - 选择最优走法

        参数:
            state: 完整游戏状态，包含棋盘、骰子、被吃棋子等信息
            valid_moves: 当前合法走法列表（已由服务器提供）

        返回:
            选中的走法
        """
        self.move_count += 1
        board = state['board']

        if not valid_moves:
            return None

        if self.strategy == 'random':
            return self.random_move(valid_moves)
        else:
            return self.greedy_move(board, valid_moves)

    def random_move(self, valid_moves: List[Move]) -> Move:
        """随机策略"""
        return random.choice(valid_moves)

    def greedy_move(self, board: Board, valid_moves: List[Move]) -> Move:
        """贪心策略：评估每步走法，选分数最高的"""
        best_move = None
        best_score = float('-inf')

        for move in valid_moves:
            score = self.evaluate_position(board, move)
            if score > best_score:
                best_score = score
                best_move = move

        return best_move or valid_moves[0]

    def evaluate_position(self, board: Board, move: Move) -> float:
        """评估走法分数"""
        tr, tc = move['to']
        score = 0.0

        # 吃子奖励
        target = board[tr][tc]
        if target:
            if target['color'] != self.color:
                score += 50  # 吃对方子：大奖励
            else:
                score -= 10  # 吃自己：轻微惩罚（有时也是好策略）

        # 位置价值
        if self.color == 'blue':
            # 蓝方目标：(0,0)，越靠近越好
            dist_to_goal = tr + tc
            score += (8 - dist_to_goal) * 4
            if tr == 0 and tc == 0:
                score += 1000  # 获胜
        else:
            # 红方目标：(4,4)
            dist_to_goal = (4 - tr) + (4 - tc)
            score += (8 - dist_to_goal) * 4
            if tr == 4 and tc == 4:
                score += 1000

        # 棋子价值（小号棋子更难替代）
        piece_id = move.get('piece_id', 3)
        score += (7 - piece_id) * 1.5

        return score


# ============================================================
# WebSocket 客户端
# ============================================================

class ExternalAIClient:
    def __init__(self, server_url: str = 'ws://localhost:8765/ai', color: str = 'blue'):
        self.server_url = server_url
        self.color = color
        self.ai = MyAI(color=color, strategy='minimax')
        self.connected = False
        self.total_moves = 0
        self.total_time = 0.0
    
    async def connect(self):
        """连接到服务器并处理消息"""
        logger.info(f"正在连接到: {self.server_url}")
        
        reconnect_delay = 1
        max_delay = 30
        
        while True:
            try:
                async with websockets.connect(
                    self.server_url,
                    ping_interval=20,
                    ping_timeout=10
                ) as ws:
                    self.connected = True
                    reconnect_delay = 1
                    # 路径 /ai 已标识身份，无需额外握手
                    logger.info(f"已连接到服务器 (路径: /ai)")
                    
                    async for message in ws:
                        await self.handle_message(ws, message)
                        
            except websockets.exceptions.ConnectionRefused:
                self.connected = False
                logger.warning(f"无法连接服务器，{reconnect_delay}秒后重试...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_delay)
                
            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                logger.info(f"连接断开，{reconnect_delay}秒后重连...")
                await asyncio.sleep(reconnect_delay)
                
            except KeyboardInterrupt:
                logger.info("AI 已停止")
                break
            except Exception as e:
                self.connected = False
                logger.error(f"连接错误: {e}")
                await asyncio.sleep(reconnect_delay)
    
    async def handle_message(self, ws, raw: str):
        """处理收到的消息"""
        try:
            data = json.loads(raw)
            msg_type = data.get('type', '')
            
            if msg_type == 'connected':
                role = data.get('role', 'unknown')
                logger.info(f"服务器确认连接，角色: {role}")

            elif msg_type == 'game_start':
                # 接收游戏开始通知（总时间等信息）
                self.ai.time_limit = data.get('time_limit', 240)
                self.ai.time_remaining = self.ai.time_limit
                logger.info(f"游戏开始! 每方时间: {self.ai.time_limit}秒, 先手: {data.get('first_player')}")

            elif msg_type == 'move_request':
                await self.handle_move_request(ws, data)
                
            elif msg_type == 'pong':
                pass  # 忽略心跳响应
                
            elif msg_type == 'game_over':
                winner = data.get('winner', '?')
                reason = data.get('reason', '')
                logger.info(f"游戏结束！获胜方: {winner} | 原因: {reason}")
                logger.info(f"本局统计: {self.total_moves} 步, 平均思考 {self.total_time/max(1,self.total_moves)*1000:.1f}ms")
                
            else:
                logger.debug(f"收到消息: {msg_type}")
                
        except json.JSONDecodeError:
            logger.error(f"无效的JSON消息")
        except Exception as e:
            logger.error(f"消息处理出错: {e}")
    
    async def handle_move_request(self, ws, data: dict):
        """处理走棋请求"""
        dice = data.get('dice')
        current_player = data.get('current_player')
        valid_moves = data.get('valid_moves', [])
        game_state_info = data.get('game_state', {})

        # 更新剩余时间信息
        if 'current_time_remaining' in game_state_info:
            self.ai.time_remaining = game_state_info['current_time_remaining']
        if 'time_limit' in game_state_info:
            self.ai.time_limit = game_state_info['time_limit']
        
        logger.info(f"收到请求: 玩家={current_player}, 骰子={dice}, "
                   f"可选走法={len(valid_moves)}, 手数={game_state_info.get('move_count',0)}")
        
        # 更新 AI 颜色（可能在游戏中途换边）
        if current_player:
            self.ai.color = current_player
        
        if not valid_moves:
            logger.warning("没有合法走法")
            await ws.send(json.dumps({"type": "error", "message": "没有合法走法"}))
            return
        
        # 打印棋盘状态（调试用）
        board = data.get('board', [])
        if board:
            logger.debug(f"当前棋盘:\n{board_to_str(board)}")
        
        # 计时
        start_time = time.time()
        
        # 选择走法
        try:
            chosen_move = self.ai.choose_move(data, valid_moves)
        except Exception as e:
            logger.error(f"AI决策出错: {e}, 随机选择")
            chosen_move = random.choice(valid_moves)
        
        elapsed = time.time() - start_time
        self.total_moves += 1
        self.total_time += elapsed
        
        if chosen_move:
            response = {
                "type": "move_response",
                "piece_id": chosen_move.get('piece_id'),
                "from": chosen_move.get('from'),
                "to": chosen_move.get('to'),
                "move_index": chosen_move.get('index', valid_moves.index(chosen_move) if chosen_move in valid_moves else 0)
            }
            logger.info(
                f"AI决策: 棋子{chosen_move.get('piece_id')} "
                f"{chosen_move.get('from')} → {chosen_move.get('to')} "
                f"(思考 {elapsed*1000:.0f}ms)"
            )
            await ws.send(json.dumps(response))
        else:
            logger.warning("AI无法选择走法")
            await ws.send(json.dumps({"type": "error", "message": "AI无法选择走法"}))


# ============================================================
# 主程序
# ============================================================

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='爱恩斯坦棋外部AI示例')
    parser.add_argument('--server', default='ws://localhost:8765/ai', help='服务器地址')
    parser.add_argument('--color', default='blue', choices=['red', 'blue'], help='棋子颜色')
    parser.add_argument('--strategy', default='greedy',
                       choices=['random', 'greedy'], help='AI策略')
    args = parser.parse_args()
    
    print("=" * 60)
    print("  爱恩斯坦棋 外部AI示例")
    print("=" * 60)
    print(f"  服务器: {args.server}")
    print(f"  颜色: {args.color}")
    print(f"  策略: {args.strategy}")
    print("  按 Ctrl+C 退出")
    print("=" * 60)
    
    client = ExternalAIClient(server_url=args.server, color=args.color)
    client.ai.strategy = args.strategy
    
    await client.connect()


if __name__ == '__main__':
    asyncio.run(main())
