import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from bot.maintenance import can_update, is_owner


class UpdatePermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'KLEE_OWNER_IDS': '7'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def guild(self, administrator=False, manage_guild=False):
        member = NS(guild_permissions=NS(administrator=administrator, manage_guild=manage_guild))
        return NS(owner_id=1, fetch_member=AsyncMock(return_value=member))

    async def test_configured_owner_keeps_access_even_in_dm(self):
        self.assertTrue(await can_update(7, None))

    async def test_server_owner_can_update_without_configured_owner_entry(self):
        guild = self.guild()
        self.assertTrue(await can_update(1, guild))
        guild.fetch_member.assert_not_awaited()

    async def test_admin_permission_is_fetched_fresh(self):
        guild = self.guild(administrator=True)
        self.assertTrue(await can_update(2, guild))
        guild.fetch_member.assert_awaited_once_with(2)

    async def test_ordinary_member_and_manage_server_only_are_denied(self):
        self.assertFalse(await can_update(2, self.guild()))
        self.assertFalse(await can_update(2, self.guild(manage_guild=True)))

    async def test_unverified_permissions_and_non_owner_dm_are_denied(self):
        guild = self.guild(administrator=True)
        guild.fetch_member.side_effect = TimeoutError()
        self.assertFalse(await can_update(2, guild))
        self.assertFalse(await can_update(2, None))

    async def test_update_permission_does_not_grant_development_owner_access(self):
        self.assertTrue(await can_update(2, self.guild(administrator=True)))
        self.assertFalse(is_owner(2))
