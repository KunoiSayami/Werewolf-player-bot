#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# player.py
# Copyright (C) 2020-2021 KunoiSayami
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
from __future__ import annotations
import ast
import asyncio
import concurrent.futures
import logging
import random
import sys
import warnings
from configparser import ConfigParser
from dataclasses import dataclass
from typing import Coroutine, Optional

from redis import asyncio as aioredis
import pyrogram
from pyrogram import Client, ContinuePropagation, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, ReplyKeyboardRemove
from pyrogram.errors import MessageIdInvalid

logger = logging.getLogger("Werewolf_bot")
logger.setLevel(logging.INFO)
logger_detail = logger.getChild("detail")
logger.setLevel(logging.INFO)


class JoinGameTracker:
    def __init__(self, client: Client, key: str):
        self.client = client
        self.key = key
        self.future: Optional[concurrent.futures.Future] = None
        self.handler = MessageHandler(
            self.message_handler, filters.chat(Players.WEREWOLF_BOT_ID) & filters.text
        )

    def cancel(self) -> None:
        if self.future is not None:
            self.future.cancel()
            self.future = None
            self.client.remove_handler(self.handler, -1)
            logger.debug("%s: Canceled!", self.client.name)

    async def _send(self) -> None:
        logger.debug("%s: Started!", self.client.name)
        self.client.add_handler(self.handler, -1)
        for x in range(3):
            await self.client.send_message(
                Players.WEREWOLF_BOT_ID, f"/start {self.key}"
            )
            await asyncio.sleep(10)

    def create_task(self) -> None:
        if self.future is None:
            self.future = asyncio.create_task(self._send())

    async def wait(self) -> None:
        if self.future is not None:
            await asyncio.wait((self.future,))

    async def message_handler(self, _client: Client, msg: Message) -> None:
        if "你已加入" in msg.text and "的遊戲中" in msg.text:
            self.cancel()
        elif "You are already in a game!" in msg.text:
            logger.info("%s, Canceled", msg.text)
            self.cancel()

    @classmethod
    def create(cls, client: Client, key: str) -> JoinGameTracker:
        self = cls(client, key)
        self.create_task()
        return self


@dataclass(init=False)
class GameConfig:
    enabled: bool
    worker_num: int
    id_cards: list[str]
    _default_worker_num: int
    group_join_string: str

    def __init__(self, enabled: bool, worker_num: int):
        self.enabled = enabled
        self.worker_num = worker_num
        self.id_cards = []
        self._default_worker_num = worker_num
        self.group_join_string = ""

    def clear_id_cards(self) -> None:
        self.id_cards.clear()

    def reset(self) -> None:
        self.enabled = True
        self.worker_num = self._default_worker_num
        self.group_join_string = ""
        self.clear_id_cards()


class Players:

    WEREWOLF_BOT_ID: int = 175844556

    def __init__(self, redis: aioredis.Redis):
        self.client_group: list[Client] = []
        self.redis = redis
        self.TARGET: str = ""
        self.FORCE_TARGET_HUMAN: bool = False
        self.lock: asyncio.Lock = asyncio.Lock()
        self._listen_to_group: list[int] = [0]
        self.owner: int = 0
        self.BOT_LIST: list[str] = []
        self.redis_key_suffix: str = "werewolf_bot"
        self.game_configs: dict[int, GameConfig] = {}
        self.game_identification_mapping: dict[str, int] = {}

    @property
    def listen_to_group(self) -> list[int]:
        return self._listen_to_group

    @listen_to_group.setter
    def listen_to_group(self, value: list[int]) -> None:
        if isinstance(value, int):
            value = [value]
            warnings.warn(
                "Passing int value to set listen group is deprecated since version 2.0.0",
                DeprecationWarning,
                2,
            )
        self._listen_to_group = value
        logger.debug("Set listen group to %s", str(self.listen_to_group))

    @classmethod
    async def create(cls) -> Players:
        logger.info("Creating bot instance")
        self = cls(await aioredis.from_url("redis://localhost"))
        config = ConfigParser()
        config.read("config.ini")
        self.listen_to_group = ast.literal_eval(config.get("account", "listen_to"))
        self.owner = config.getint("account", "owner", fallback=0)
        for _x in range(config.getint("account", "count")):
            self.client_group.append(
                Client(
                    f"werewolf{_x}",
                    api_id=config.getint("account", "api_id"),
                    api_hash=config.get("account", "api_hash"),
                    app_version="werewolf",
                )
            )
        worker_num = len(self.client_group)
        for group in self.listen_to_group:
            self.game_configs.update({group: GameConfig(True, worker_num)})
        self.redis_key_suffix = config.get(
            "account", "redis_key_suffix", fallback="werewolf_bot"
        )
        self.init_message_handler()
        return self

    def init_message_handler(self) -> None:
        if self._listen_to_group[0] == 0:
            raise ValueError("listen_to_group value must be set")
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_set_target,
                filters.chat(self.owner) & filters.command("target"),
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_resend_command,
                filters.user(self.owner)
                & filters.chat(self._listen_to_group)
                & filters.command("resend"),
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_toggle_debug_command,
                filters.chat(self.owner) & filters.command("debug"),
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_normal_resident,
                filters.chat(self._listen_to_group)
                & filters.user(self.WEREWOLF_BOT_ID)
                & filters.text,
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_join_game,
                filters.chat(self._listen_to_group)
                & filters.user(self.WEREWOLF_BOT_ID),
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_close_auto_join,
                filters.chat(self._listen_to_group) & filters.command("off"),
            )
        )
        self.client_group[0].add_handler(
            MessageHandler(
                self.handle_set_num_worker,
                filters.chat(self._listen_to_group) & filters.command("setw"),
            )
        )
        for x in self.client_group:
            x.add_handler(
                MessageHandler(
                    self.handle_werewolf_game,
                    filters.chat(self.WEREWOLF_BOT_ID) & filters.incoming,
                )
            )
        logger.debug("Current workers: %d", len(self.client_group))

    @staticmethod
    async def safe_start_or_stop(client: Client, method: Coroutine) -> Optional[str]:
        try:
            await method
        except (
            pyrogram.errors.UserDeactivated,
            pyrogram.errors.UserDeactivatedBan,
        ) as e:
            logger.critical(
                "Client is deactivated%s, please re-login: %s",
                (
                    " and got banned"
                    if isinstance(e, pyrogram.errors.UserDeactivatedBan)
                    else ""
                ),
                client.name,
            )
            return client.name

    async def start(self) -> None:
        logger.info("Starting clients")
        results = await asyncio.gather(
            *(self.safe_start_or_stop(x, x.start()) for x in self.client_group)
        )

        fail_count = 0
        for result in results:
            if result is not None:
                for client in self.client_group:
                    if result == client.name:
                        self.client_group.remove(client)
                        fail_count += 1
                        break
        if fail_count > 0:
            for group_id, config in self.game_configs.items():
                config.worker_num = len(self.client_group)
            logger.warning(
                "Found %d client(s) couldn't start, resize worker num to %d",
                fail_count,
                len(self.client_group),
            )

        self.BOT_LIST.clear()
        self.BOT_LIST.extend(
            map(
                lambda u: str(u.id),
                await asyncio.gather(*(x.get_me() for x in self.client_group)),
            )
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(self.safe_start_or_stop(x, x.stop()) for x in self.client_group)
        )
        await self.redis.close()

    async def run(self) -> None:
        await self.start()
        logger.info("Listening game status")
        await pyrogram.idle()

    async def handle_set_target(self, _client: Client, msg: Message) -> None:
        if len(msg.command) > 1:
            if msg.command[1] == "h":
                self.FORCE_TARGET_HUMAN = not self.FORCE_TARGET_HUMAN
                await msg.reply(f"Set force target human to {self.FORCE_TARGET_HUMAN}")
            else:
                self.TARGET = msg.command[1]
                await msg.reply(f"Target set to: {self.TARGET}")
        else:
            self.TARGET = ""
            await msg.reply("Target cleared")
        raise ContinuePropagation

    async def handle_resend_command(self, _client: Client, msg: Message) -> None:
        obj = await self.redis.get(f"{self.redis_key_suffix}_{msg.chat.id}")
        if obj is not None:
            obj = obj.decode()
        else:
            return
        if len(msg.command) > 1:
            for client in self.client_group:
                if client.name == msg.command[1]:
                    await client.send_message(self.WEREWOLF_BOT_ID, f"/start {obj}")

    async def handle_toggle_debug_command(self, _client: Client, msg: Message) -> None:
        logger.setLevel(
            logging.INFO if logger.level == logging.DEBUG else logging.DEBUG
        )
        msg = await msg.reply(
            f'Set level to {"DEBUG" if logger.level == logging.DEBUG else "INFO"}'
        )
        await asyncio.sleep(5)
        await msg.delete()

    async def handle_join_game(self, _client: Client, msg: Message) -> None:
        instance = self.game_configs[msg.chat.id]
        self.TARGET = ""
        self.FORCE_TARGET_HUMAN = False
        if (
            msg.reply_markup
            and msg.reply_markup.inline_keyboard
            and (
                any(
                    msg.reply_markup.inline_keyboard[0][0].text == x
                    for x in ["加入遊戲", "Join"]
                )
            )
        ):
            obj = await self.redis.get(f"{self.redis_key_suffix}_{msg.chat.id}")
            if obj is not None:
                obj = obj.decode()
                if not instance.enabled:
                    return
            instance.clear_id_cards()
            link = msg.reply_markup.inline_keyboard[0][0].url.split("=")[1]
            if obj == link:
                return
            if instance.group_join_string in self.game_identification_mapping:
                self.game_identification_mapping.pop(instance.group_join_string)
            instance.group_join_string = link
            waiter = asyncio.gather(
                *(
                    JoinGameTracker.create(self.client_group[x], link).wait()
                    for x in range(self.game_configs[msg.chat.id].worker_num)
                )
            )
            logger.info("Joined the game %s", link)
            await self.redis.set(f"{self.redis_key_suffix}_{msg.chat.id}", link)
            await waiter
        raise ContinuePropagation

    async def handle_set_num_worker(self, _client: Client, msg: Message) -> None:
        if len(msg.command) > 1:
            try:
                worker_num = int(msg.command[1])
                if worker_num > len(self.client_group) or worker_num < 1:
                    raise ValueError
                self.game_configs[msg.chat.id].worker_num = worker_num
                return
            except ValueError:
                pass
        msg = await msg.reply("Please check your input")
        await asyncio.sleep(5)
        await msg.delete()

    async def handle_normal_resident(self, _client: Client, msg: Message) -> None:
        if any(
            x in msg.text
            for x in [
                "和事佬",
                "銀渣",
                "哼着",
                "回到家中哼起",
                "出示了來自官方",
                "捣蛋",
                "一聲槍聲",
            ]
        ):
            logger.debug(repr(msg))
            instance = self.game_configs[msg.chat.id].id_cards
            for x in msg.entities:
                if x.type == "text_mention" and str(x.user.id) not in instance:
                    logger.debug("Insert %d to HAS_ID_CARD array", x.user.id)
                    instance.append(str(x.user.id))
        raise ContinuePropagation

    async def handle_close_auto_join(self, _client: Client, msg: Message) -> None:
        instance = self.game_configs[msg.chat.id]
        instance.enabled = not instance.enabled
        if instance.enabled:
            _msg = await msg.reply("Started")
        else:
            _msg = await msg.reply("Stopped")
        await asyncio.sleep(5)
        await _msg.delete()
        raise ContinuePropagation

    async def handle_werewolf_game(self, client: Client, msg: Message) -> None:
        client_id: str = client.name
        if msg.text:
            logger.info("%s: %s", client.name, msg.text)
        if msg.caption:
            logger.info("%s: %s", client.name, msg.caption)
        if isinstance(msg.reply_markup, ReplyKeyboardRemove):
            raise ContinuePropagation
        if not (msg.reply_markup and msg.reply_markup.inline_keyboard):
            raise ContinuePropagation
        await asyncio.sleep(random.randint(5, 15))
        async with self.lock:
            # Get group identification string from inline keyboard callback data
            group_id_str = msg.reply_markup.inline_keyboard[0][0].callback_data.split(
                "|"
            )[2]
            if not (group_id := self.game_identification_mapping.get(group_id_str)):
                for group_id, config in self.game_configs.items():
                    if group_id_str in config.group_join_string:
                        self.game_identification_mapping.update(
                            {group_id_str: group_id}
                        )
                        config.group_join_string = group_id_str
                        break

            non_bot_button_loc: list[int] = []
            menu_length = len(msg.reply_markup.inline_keyboard)
            for x in range(0, menu_length):
                if any(
                    u in msg.reply_markup.inline_keyboard[x][0].callback_data
                    for u in self.BOT_LIST
                ):
                    continue
                non_bot_button_loc.append(x)
            _FORCE_HUMAN = (
                self.FORCE_TARGET_HUMAN
                or not random.randint(0, 9)
                or (menu_length < 4 and not random.randint(0, 6))
            )
            _HAS_TARGET = not self.FORCE_TARGET_HUMAN and self.TARGET != ""
            logger.debug(
                "%s: STATUS FORCE_HUMAN: %s, _HAS_TARGET: %s",
                client_id,
                _FORCE_HUMAN,
                _HAS_TARGET,
            )
            if msg.reply_markup:
                logger_detail.debug("%s", repr(msg.reply_markup))
            group_id_card_instance = self.game_configs[group_id].id_cards
            while True:
                try:
                    if len(msg.reply_markup.inline_keyboard) < 2:
                        if not random.randint(0, 3):
                            await msg.click()

                    fail_check = 0
                    while True:
                        final_choose = random.randint(0, menu_length - 1)
                        logger.debug("%s: random choose: %d", client_id, final_choose)
                        if (
                            msg.text
                            and msg.text.startswith("你想處死誰")
                            and _FORCE_HUMAN
                        ):
                            if len(non_bot_button_loc):
                                final_choose = random.choice(non_bot_button_loc)
                                logger.debug(
                                    "%s: Redirect choose to %d", client_id, final_choose
                                )
                        if _HAS_TARGET:
                            for x in range(0, menu_length):
                                if (
                                    self.TARGET
                                    in msg.reply_markup.inline_keyboard[x][
                                        0
                                    ].text.lower()
                                ):
                                    logger.debug(
                                        "Find target %s",
                                        msg.reply_markup.inline_keyboard[x][
                                            0
                                        ].callback_data,
                                    )
                                    final_choose = x
                                    break
                        elif (
                            len(group_id_card_instance)
                            and any(
                                x
                                in msg.reply_markup.inline_keyboard[final_choose][
                                    0
                                ].callback_data
                                for x in group_id_card_instance
                            )
                            and fail_check < 2
                        ):
                            logger.debug(
                                "%s: Got HAS_ID_CARD target, Try choose target again",
                                client.name,
                            )

                            fail_check += 1
                            continue
                        logger.debug("%s: final choose: %d", client_id, final_choose)
                        for retries in range(1, 4):
                            try:
                                await msg.click(final_choose)
                                break
                            except MessageIdInvalid:
                                logger.warning(
                                    "%s: Got MessageIdInvalid (retries: %d)",
                                    client_id,
                                    retries,
                                )
                        break
                    # logger.debug(repr(msg))
                    break
                except TimeoutError:
                    pass


async def main() -> None:
    p = await Players.create()
    await p.run()
    await p.stop()


if __name__ == "__main__":
    try:
        import coloredlogs

        coloredlogs.install(
            logging.DEBUG,
            fmt="%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s",
        )
    except ModuleNotFoundError:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s",
        )

    if "--debug" in sys.argv:
        logger.setLevel(logging.DEBUG)
        logger.info("Program is running under debug mode")
    if "--detail" in sys.argv:
        logger_detail.setLevel(logging.DEBUG)
        logger_detail.info("Program will show more detail information")
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    asyncio.get_event_loop().run_until_complete(main())
