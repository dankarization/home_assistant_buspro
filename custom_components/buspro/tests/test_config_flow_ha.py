import asyncio
import unittest
from types import SimpleNamespace

from tests.bootstrap import ensure_homeassistant_stubs

ensure_homeassistant_stubs()

from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME

from custom_components.buspro import config_flow as cf
from custom_components.buspro.const import (
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_MANAGED_DEVICES,
    CONF_PORT,
    CONF_SEND_PORT,
    CONF_RECEIVE_PORT,
    CONF_CLIENT_ADDRESS,
    DEFAULT_CLIENT_ADDRESS,
    DEVICE_TYPE_MIXED_OUTPUT,
    DEVICE_TYPE_MULTISENSOR,
)

_VALID_INPUT = {
    CONF_HOST: '192.168.1.10',
    CONF_PORT: 6000,
    CONF_SEND_PORT: 6000,
    CONF_RECEIVE_PORT: 6000,
    CONF_CLIENT_ADDRESS: DEFAULT_CLIENT_ADDRESS,
}


class _FakeEntry:
    def __init__(self, entry_id='e1', data=None, options=None):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.state = None  # not ConfigEntryState.LOADED → probe_socket=True in reconfigure
        self.unique_id = None


class _FakeConfigEntries:
    def __init__(self):
        self.entries = {}
        self.updated = []
        self.reloaded = []

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)

    def async_update_entry(self, entry, data=None, options=None, **kwargs):
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options
        self.updated.append((entry.entry_id, data, options))

    async def async_reload(self, entry_id):
        self.reloaded.append(entry_id)


class _FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.config = SimpleNamespace(language='en')
        self.config_entries = _FakeConfigEntries()
        self.existing_unique_ids = set()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.orig_validate = cf._async_validate_connectivity

    async def asyncTearDown(self):
        cf._async_validate_connectivity = self.orig_validate

    async def test_user_step_success_creates_entry(self):
        async def ok_validate(hass, data, probe_socket=True):
            return None

        cf._async_validate_connectivity = ok_validate
        flow = cf.ConfigFlow()
        flow.hass = _FakeHass()

        result = await flow.async_step_manual({**_VALID_INPUT})

        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['title'], 'Buspro (192.168.1.10)')
        self.assertEqual(result['data'][CONF_HOST], '192.168.1.10')

    async def test_user_step_invalid_host_shows_error(self):
        async def bad_validate(hass, data, probe_socket=True):
            raise cf.InvalidHost()

        cf._async_validate_connectivity = bad_validate
        flow = cf.ConfigFlow()
        flow.hass = _FakeHass()

        result = await flow.async_step_manual({
            CONF_HOST: 'bad-host',
            CONF_PORT: 6000,
            CONF_CLIENT_ADDRESS: DEFAULT_CLIENT_ADDRESS,
        })

        self.assertEqual(result['type'], 'form')
        self.assertEqual(result['errors'].get('base'), 'invalid_host')

    async def test_options_flow_init_shows_menu(self):
        entry = _FakeEntry(data={CONF_HOST: '1.1.1.1', CONF_PORT: 6000})
        flow = cf.BusproOptionsFlow(entry)
        flow.hass = _FakeHass()

        result = await flow.async_step_init(None)

        self.assertEqual(result['type'], 'menu')
        self.assertIn('gateway', result['menu_options'])

    async def test_mhiou_mixed_output_options_flow(self):
        entry = _FakeEntry(
            data={CONF_HOST: '1.1.1.1', CONF_PORT: 6000},
            options={},
        )
        flow = cf.BusproOptionsFlow(entry)
        flow.hass = _FakeHass()

        original_schema = cf.vol.Schema
        original_select = cf.selector.SelectSelector
        had_select_mode = hasattr(cf.selector, 'SelectSelectorMode')
        original_select_mode = getattr(cf.selector, 'SelectSelectorMode', None)
        cf.vol.Schema = lambda value, *args, **kwargs: value
        cf.selector.SelectSelector = lambda config=None: config
        cf.selector.SelectSelectorMode = SimpleNamespace(DROPDOWN='dropdown')
        try:
            add_result = await flow.async_step_add_device()
            device_type_selector = add_result['data_schema'][CONF_DEVICE_TYPE]
            self.assertIn(
                DEVICE_TYPE_MIXED_OUTPUT,
                device_type_selector['options'],
            )

            details_result = await flow.async_step_add_device(
                {CONF_DEVICE_TYPE: DEVICE_TYPE_MIXED_OUTPUT}
            )
            model_selector = details_result['data_schema'][CONF_MODEL]
            self.assertEqual(
                model_selector['options'],
                ['HDL-MHIOU.432', 'HDL-MHIOU-II.432'],
            )

            channels_result = await flow.async_step_device_details({
                CONF_ADDRESS: '1.2',
                CONF_NAME: 'Mixed output module',
                CONF_MODEL: 'HDL-MHIOU.432',
            })
            self.assertEqual(channels_result['step_id'], 'device_channels')
            self.assertEqual(
                set(channels_result['data_schema']),
                {f'channel_{channel}' for channel in range(1, 13)},
            )

            saved_result = await flow.async_step_device_channels({
                f'channel_{channel}': f'Output {channel}'
                for channel in range(1, 13)
            })
            device = saved_result['data'][CONF_MANAGED_DEVICES][0]
            self.assertEqual(device[CONF_MODEL], 'HDL-MHIOU.432')
            self.assertEqual(len(device['channels']), 12)
            self.assertEqual(
                [channel[CONF_DEVICE_TYPE] for channel in device['channels']],
                ['dimmer', 'dimmer'] + ['relay'] * 10,
            )
        finally:
            cf.vol.Schema = original_schema
            cf.selector.SelectSelector = original_select
            if had_select_mode:
                cf.selector.SelectSelectorMode = original_select_mode
            else:
                del cf.selector.SelectSelectorMode

    async def test_panel_options_flow_has_no_unverified_sensor_channels(self):
        entry = _FakeEntry(
            data={CONF_HOST: '1.1.1.1', CONF_PORT: 6000},
            options={},
        )
        flow = cf.BusproOptionsFlow(entry)
        flow.hass = _FakeHass()

        original_schema = cf.vol.Schema
        original_select = cf.selector.SelectSelector
        had_select_mode = hasattr(cf.selector, 'SelectSelectorMode')
        original_select_mode = getattr(cf.selector, 'SelectSelectorMode', None)
        cf.vol.Schema = lambda value, *args, **kwargs: value
        cf.selector.SelectSelector = lambda config=None: config
        cf.selector.SelectSelectorMode = SimpleNamespace(DROPDOWN='dropdown')
        try:
            await flow.async_step_add_device(
                {CONF_DEVICE_TYPE: DEVICE_TYPE_MULTISENSOR}
            )
            details = await flow.async_step_device_details()
            self.assertIn('HDL-MP8B.46-A',
                          details['data_schema'][CONF_MODEL]['options'])
            self.assertIn('HDL-MPL8.46-A',
                          details['data_schema'][CONF_MODEL]['options'])

            channels = await flow.async_step_device_details({
                CONF_ADDRESS: '1.6',
                CONF_NAME: 'Kitchen wall',
                CONF_MODEL: 'HDL-MP8B.46-A',
            })
            self.assertEqual(channels['step_id'], 'device_channels')
            self.assertEqual(channels['data_schema'], {})

            saved = await flow.async_step_device_channels({})
            device = saved['data'][CONF_MANAGED_DEVICES][0]
            self.assertEqual(device[CONF_MODEL], 'HDL-MP8B.46-A')
            self.assertEqual(device['channels'], [])

            flow = cf.BusproOptionsFlow(entry)
            flow.hass = _FakeHass()
            await flow.async_step_add_device(
                {CONF_DEVICE_TYPE: DEVICE_TYPE_MULTISENSOR}
            )
            channels = await flow.async_step_device_details({
                CONF_ADDRESS: '1.12',
                CONF_NAME: 'ENTRY',
                CONF_MODEL: 'HDL-MPL8.46-A',
            })
            self.assertEqual(channels['step_id'], 'device_channels')
            self.assertEqual(channels['data_schema'], {})
            saved = await flow.async_step_device_channels({})
            device = saved['data'][CONF_MANAGED_DEVICES][0]
            self.assertEqual(device[CONF_MODEL], 'HDL-MPL8.46-A')
            self.assertEqual(device['channels'], [])
        finally:
            cf.vol.Schema = original_schema
            cf.selector.SelectSelector = original_select
            if had_select_mode:
                cf.selector.SelectSelectorMode = original_select_mode
            else:
                del cf.selector.SelectSelectorMode

    async def test_options_flow_gateway_saves_data(self):
        async def ok_validate(hass, data, probe_socket=True):
            return None

        cf._async_validate_connectivity = ok_validate
        entry = _FakeEntry(data={CONF_HOST: '1.1.1.1', CONF_PORT: 6000})
        flow = cf.BusproOptionsFlow(entry)
        flow.hass = _FakeHass()

        result = await flow.async_step_gateway({
            CONF_HOST: '2.2.2.2',
            CONF_PORT: 6001,
            CONF_SEND_PORT: 6001,
            CONF_RECEIVE_PORT: 6001,
            CONF_CLIENT_ADDRESS: DEFAULT_CLIENT_ADDRESS,
        })

        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(entry.data[CONF_HOST], '2.2.2.2')
        self.assertEqual(entry.data[CONF_PORT], 6001)
        self.assertNotIn(CONF_HOST, result['data'])

    async def test_reconfigure_updates_and_reloads(self):
        async def ok_validate(hass, data, probe_socket=True):
            return None

        cf._async_validate_connectivity = ok_validate
        hass = _FakeHass()
        entry = _FakeEntry(
            entry_id='entry-1',
            data={CONF_HOST: '10.0.0.1', CONF_PORT: 6000},
            options={},
        )
        hass.config_entries.entries[entry.entry_id] = entry

        flow = cf.ConfigFlow()
        flow.hass = hass
        flow.context = {'entry_id': entry.entry_id}

        result = await flow.async_step_reconfigure({
            CONF_HOST: '10.0.0.2',
            CONF_PORT: 6002,
            CONF_SEND_PORT: 6002,
            CONF_RECEIVE_PORT: 6002,
            CONF_CLIENT_ADDRESS: DEFAULT_CLIENT_ADDRESS,
        })

        self.assertEqual(result['type'], 'abort')
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual(entry.data[CONF_HOST], '10.0.0.2')
        self.assertIn(entry.entry_id, hass.config_entries.reloaded)


if __name__ == '__main__':
    unittest.main()
