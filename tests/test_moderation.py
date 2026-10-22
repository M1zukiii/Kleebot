from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from bot.moderation import Moderation, ModerationError, apply_timeout, publish_receipt


def member(uid, guild, role, moderate=False, administrator=False, bot=False):
    return SimpleNamespace(id=uid, guild=guild, top_role=role, bot=bot,
        guild_permissions=SimpleNamespace(moderate_members=moderate, administrator=administrator),
        timeout=AsyncMock(), is_timed_out=lambda: True)


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=10, owner_id=99)
        self.actor = member(1, self.guild, 10, moderate=True)
        self.target = member(2, self.guild, 1)
        self.bot_member = member(3, self.guild, 20, moderate=True, bot=True)
        self.guild.me = self.bot_member
        members = {m.id: m for m in (self.actor, self.target, self.bot_member)}
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: members[uid])
        self.permissions = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        self.channel = SimpleNamespace(permissions_for=lambda _: self.permissions, send=AsyncMock())

    async def apply(self, minutes=10, reason='连续刷屏'):
        return await apply_timeout(self.guild, self.actor, self.target, self.bot_member, self.channel, minutes, reason)

    async def test_real_timeout_arguments_and_public_duration_reason(self):
        before = datetime.now(timezone.utc).timestamp()
        receipt = await self.apply()
        self.target.timeout.assert_awaited_once()
        call = self.target.timeout.await_args
        self.assertAlmostEqual(call.args[0].timestamp() - before, 600, delta=3)
        self.assertIn('1', call.kwargs['reason'])
        self.assertIn('连续刷屏', call.kwargs['reason'])
        await publish_receipt(self.channel, self.bot_member, receipt)
        sent = self.channel.send.await_args.kwargs
        fields = {field.name: field.value for field in sent['embed'].fields}
        self.assertEqual(fields['禁言时长'], '10分钟')
        self.assertEqual(fields['原因'], '连续刷屏')
        self.assertIn('解除时间', fields)
        self.assertEqual(sent['allowed_mentions'].to_dict()['parse'], [])

    async def test_unprivileged_actor_cannot_call_discord_or_announce(self):
        self.actor.guild_permissions.moderate_members = False
        with self.assertRaises(ModerationError):
            await self.apply()
        self.guild.fetch_member.assert_not_awaited()
        self.target.timeout.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_permission_revoked_since_message_cannot_call_timeout(self):
        revoked = member(1, self.guild, 10)
        self.guild.fetch_member.side_effect = lambda uid: {1: revoked, 2: self.target, 3: self.bot_member}[uid]
        with self.assertRaises(ModerationError):
            await self.apply()
        self.target.timeout.assert_not_awaited()

    async def test_hierarchy_admin_owner_and_bot_protection(self):
        for field, value in [('top_role', 10), ('id', 99), ('bot', True)]:
            previous = getattr(self.target, field)
            setattr(self.target, field, value)
            with self.assertRaises(ModerationError):
                await self.apply()
            setattr(self.target, field, previous)
        self.target.guild_permissions.administrator = True
        with self.assertRaises(ModerationError):
            await self.apply()
        self.target.timeout.assert_not_awaited()

    async def test_bot_permission_and_role_are_required(self):
        self.bot_member.guild_permissions.moderate_members = False
        with self.assertRaises(ModerationError):
            await self.apply()
        self.bot_member.guild_permissions.moderate_members = True
        self.bot_member.top_role = 1
        with self.assertRaises(ModerationError):
            await self.apply()
        self.target.timeout.assert_not_awaited()

    async def test_bounds_and_reason_checked_before_effect(self):
        for minutes in (0, -1, 40321, True):
            with self.assertRaises(ModerationError):
                await self.apply(minutes=minutes)
        for reason in ('', ' ', 'x' * 401):
            with self.assertRaises(ModerationError):
                await self.apply(reason=reason)
        self.target.timeout.assert_not_awaited()

    async def test_cannot_mute_if_public_announcement_is_forbidden(self):
        self.permissions.send_messages = False
        with self.assertRaisesRegex(ModerationError, '公告'):
            await self.apply()
        self.target.timeout.assert_not_awaited()

    async def test_plain_text_fallback_and_mentions_disabled(self):
        receipt = await self.apply(reason='不要 @everyone 刷屏')
        self.permissions.embed_links = False
        await publish_receipt(self.channel, self.bot_member, receipt)
        args = self.channel.send.await_args
        self.assertIn('10分钟', args.args[0])
        self.assertIn('不要 @everyone 刷屏', args.args[0])
        self.assertEqual(args.kwargs['allowed_mentions'].to_dict()['parse'], [])

    async def test_unmute_announces_reason_and_sends_none(self):
        receipt = await self.apply(minutes=None, reason='已确认停止刷屏')
        self.assertIsNone(self.target.timeout.await_args.args[0])
        self.assertIn('解除禁言', receipt.text())
        self.assertIn('已确认停止刷屏', receipt.text())

    async def test_discord_denial_never_posts_success_receipt(self):
        self.target.timeout.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        cog = Moderation(None)
        with self.assertRaises(discord.Forbidden):
            await cog.execute(self.guild, self.actor, self.target, self.channel, 10, '刷屏')
        self.channel.send.assert_not_awaited()

    async def test_cog_registers_slash_and_prefix_commands(self):
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        async with bot:
            await bot.add_cog(Moderation(bot))
            self.assertIsNotNone(bot.tree.get_command('禁言'))
            self.assertIsNotNone(bot.tree.get_command('解除禁言'))
            self.assertIsNotNone(bot.get_command('禁言'))
            self.assertIsNotNone(bot.get_command('解除禁言'))


if __name__ == '__main__':
    unittest.main()
