#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# player.py
# Copyright (C) 2020 KunoiSayami
#
# This module is part of Werewolf-player-bot and is released under
# the AGPL v3 License: https://www.gnu.org/licenses/agpl-3.0.txt
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
import asyncio
import logging
import random
from configparser import ConfigParser
from typing import Dict, List, Union

import aioredis
import pyrogram
from pyrogram import Client, ContinuePropagation, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, ReplyKeyboardRemove

logger = logging.getLogger('Werewolf_bot')
logger.setLevel(logging.INFO)


class Players:

    WEREWOLF_BOT_ID: int = 175844556

    def __init__(self, redis: aioredis.Redis):
        self.client_group: List[Client] = []
        self.client_map: Dict[int, Client] = {}
        self.client_map_rev: Dict[Client, int] = {}
        self.redis = redis
        self.TARGET: str = ''
        self.FORCE_TARGET_HUMAN: bool = False
        self.HAS_ID_CARD: List[str] = []
        self.lock: asyncio.Lock = asyncio.Lock()
        self.listen_to_group: int = 0
        self.owner: int = 0
        self.worker_num: int = 0
        self.join_game: bool = True
        self.BOT_LIST: List[str] = []

    def _set_group_listen(self, listen_to_group: Union[str, int]) -> None:
        self.listen_to_group = int(listen_to_group)
        logger.debug('Sent listen group to %d', self.listen_to_group)

    @classmethod
    async def create(cls):
        logger.info('Creating bot instance')
        self = cls(await aioredis.create_redis_pool('redis://localhost'))
        config = ConfigParser()
        config.read('config.ini')
        self._set_group_listen(config.getint('account', 'listen_to'))
        self.owner = config.getint('account', 'owner', fallback=0)
        for _x in range(config.getint('account', 'count')):
            self.client_group.append(
                Client(f'werewolf{_x}', api_id=config.getint('account', 'api_id'),
                       api_hash=config.get('account', 'api_hash'),
                       app_version='werewolf')
            )
            self.client_map.update({_x: self.client_group[-1]})
        self.client_map_rev.update({v: k for k, v in self.client_map.items()})
        self.worker_num = len(self.client_group)
        self.init_message_handler()
        return self

    def init_message_handler(self) -> None:
        if self.listen_to_group == 0:
            raise ValueError('listen_to_group value must be set')
        self.client_group[0].add_handler(MessageHandler(self.handle_set_target,
                                                        filters.chat(self.owner) & filters.command('target')))
        self.client_group[0].add_handler(MessageHandler(self.handle_resend_command,
                                                        filters.chat(self.owner) & filters.command('resend')))
        self.client_group[0].add_handler(MessageHandler(self.handle_normal_resident,
                                                        filters.chat(self.listen_to_group) &
                                                        filters.user(self.WEREWOLF_BOT_ID) & filters.text))
        self.client_group[0].add_handler(MessageHandler(self.handle_join_game,
                                                        filters.chat(self.listen_to_group) &
                                                        filters.user(self.WEREWOLF_BOT_ID)))
        self.client_group[0].add_handler(MessageHandler(self.handle_close_auto_join,
                                                        filters.chat(self.listen_to_group) & filters.command('off')))
        self.client_group[0].add_handler(MessageHandler(self.handle_set_num_worker,
                                                        filters.chat(self.listen_to_group) & filters.command('setw')))
        for x in self.client_group:
            x.add_handler(MessageHandler(self.handle_werewolf_game,
                                         filters.chat(self.WEREWOLF_BOT_ID) & filters.incoming))

    async def start(self) -> None:
        logger.info('Starting clients')
        await asyncio.gather(*(x.start() for x in self.client_group))
        self.BOT_LIST.clear()
        self.BOT_LIST.extend(map(lambda u: str(u.id), await asyncio.gather(*(x.get_me() for x in self.client_group))))

    async def stop(self) -> None:
        await asyncio.gather(*(x.stop() for x in self.client_group))
        self.redis.close()
        await self.redis.wait_closed()

    async def run(self) -> None:
        await self.start()
        logger.info('Listening game status')
        await pyrogram.idle()

    async def handle_set_target(self, _client: Client, msg: Message) -> None:
        if len(msg.command) > 1:
            if msg.command[1] == 'h':
                self.FORCE_TARGET_HUMAN = not self.FORCE_TARGET_HUMAN
                await msg.reply(f'Set force target human to {self.FORCE_TARGET_HUMAN}')
            else:
                self.TARGET = msg.command[1]
                await msg.reply(f'Target set to: {self.TARGET}')
        else:
            self.TARGET = ''
            await msg.reply('Target cleared')
        raise ContinuePropagation

    async def handle_resend_command(self, _client: Client, msg: Message) -> None:
        obj = await self.redis.get('admin_last_game')
        if obj is not None:
            obj = obj.decode()
        else:
            return
        if len(msg.command) > 1:
            await self.client_map.get(int(msg.command[1])).send_message(self.WEREWOLF_BOT_ID, f'/start {obj}')

    async def handle_join_game(self, _client: Client, msg: Message) -> None:
        self.TARGET = ''
        self.FORCE_TARGET_HUMAN = False
        if msg.reply_markup and msg.reply_markup.inline_keyboard and \
                msg.reply_markup.inline_keyboard[0][0].text == '加入遊戲':
            obj = await self.redis.get('admin_last_game')
            if obj is not None:
                obj = obj.decode()
                if not self.join_game:
                    return
            self.HAS_ID_CARD.clear()
            link = msg.reply_markup.inline_keyboard[0][0].url.split('=')[1]
            if obj == link:
                return
            await asyncio.gather(*(x.send_message(self.WEREWOLF_BOT_ID, f'/start {link}') for x in self.client_group))
            logger.info('Joined the game %s', link)
            await self.redis.set('admin_last_game', link)
        raise ContinuePropagation

    async def handle_set_num_worker(self, _client: Client, msg: Message) -> None:
        if len(msg.command) > 1:
            try:
                self.worker_num = int(msg.command[1])
                if self.worker_num > len(self.client_group) or self.worker_num < 1:
                    raise ValueError
                return
            except ValueError:
                pass
        await msg.reply('Please check your input')
        await asyncio.sleep(5)
        await msg.delete()

    async def handle_normal_resident(self, _client: Client, msg: Message) -> None:
        if any(x in msg.text for x in ['和事佬', '撒著閃亮的銀渣', '哼着', '村长', '捣蛋', '一聲槍聲']):
            if msg.entities[0].type == 'text_mention':
                self.HAS_ID_CARD.extend(map(str, (x.user.id for x in msg.entities if x.user)))
        raise ContinuePropagation

    async def handle_close_auto_join(self, _client: Client, msg: Message) -> None:
        self.join_game = not self.join_game
        if self.join_game:
            _msg = await msg.reply('Started')
        else:
            _msg = await msg.reply('Stopped')
        await asyncio.sleep(5)
        await _msg.delete()
        raise ContinuePropagation

    async def handle_werewolf_game(self, client: Client, msg: Message) -> None:
        if isinstance(msg.reply_markup, ReplyKeyboardRemove):
            raise ContinuePropagation
        async with self.lock:
            while True:
                try:
                    if msg.reply_markup and msg.reply_markup.inline_keyboard:
                        if len(msg.reply_markup.inline_keyboard) < 2:
                            if not random.randint(0, 3):
                                await msg.click()
                        non_bot: List[int] = []

                        choose_length = len(msg.reply_markup.inline_keyboard)
                        for x in range(0, choose_length):
                            if any(u in msg.reply_markup.inline_keyboard[x][0].callback_data for u in self.BOT_LIST):
                                continue
                            non_bot.append(x)
                        fail_check = 0
                        while True:
                            r = random.randint(0, choose_length - 1)
                            if msg.text and msg.text.startswith('你想處死誰') and (
                                    self.FORCE_TARGET_HUMAN or not random.randint(0, 5) or (
                                    choose_length < 5 and not random.randint(0, 3))):
                                if len(non_bot):
                                    r = random.choice(non_bot)
                            if not self.FORCE_TARGET_HUMAN and self.TARGET != '':
                                for x in range(0, choose_length):
                                    if self.TARGET in msg.reply_markup.inline_keyboard[x][0].text.lower():
                                        logger.debug('Find target %s', msg.reply_markup.inline_keyboard[x][0].callback_data)
                                        r = x
                                        break
                            elif (any(
                                    x in msg.reply_markup.inline_keyboard[r][0].callback_data for x in self.HAS_ID_CARD) and
                                    fail_check < 2):
                                fail_check += 1
                                continue
                            await msg.click(r)
                            break
                        logger.debug(repr(msg))
                    if msg.text:
                        logger.info('%d: %s', self.client_map_rev.get(client), msg.text)
                    break
                except TimeoutError:
                    pass


async def main() -> None:
    p = await Players.create()
    await p.run()
    await p.stop()


if __name__ == '__main__':
    try:
        import coloredlogs
        coloredlogs.install(logging.DEBUG,
                            fmt='%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s')
    except ModuleNotFoundError:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s')
    logging.getLogger('pyrogram').setLevel(logging.WARNING)
    asyncio.get_event_loop().run_until_complete(main())
