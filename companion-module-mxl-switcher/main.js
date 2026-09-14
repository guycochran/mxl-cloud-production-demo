// companion-module-mxl-switcher — Bitfocus Companion module for the MXL
// cloud switcher (this repo). Physical Stream Deck buttons with REAL
// program tally: buttons glow red when their source is on air, dim when
// the source has no feed. Polls /api/mxl/status once a second.
//
// The standards arc this module bridges: today it speaks the facility's
// REST dialect; the destination is an NMOS controller lane per AMWA
// BCP-007-03 (urn:x-nmos:transport:mxl) — see docs/FIELD-NOTES.md §4.

const { InstanceBase, runEntrypoint, InstanceStatus, combineRgb } = require('@companion-module/base')

const SLOTS = [
	{ id: 0, label: 'Cam 1' },
	{ id: 1, label: 'Playout' },
	{ id: 2, label: 'Pattern' },
	{ id: 3, label: 'Cam 2' },
	{ id: 4, label: 'Guest 1' },
	{ id: 5, label: 'Guest 2' },
	{ id: 6, label: 'Layout' },
]
const SLOT_CHOICES = SLOTS.map((s) => ({ id: s.id, label: s.label }))
const LAYOUT_SRC = ['cam', 'cam2', 'playout', 'pattern', 'guest1', 'guest2']
const SRC_CHOICES = LAYOUT_SRC.map((s) => ({ id: s, label: s }))

class MXLSwitcherInstance extends InstanceBase {
	async init(config) {
		this.config = config
		this.state = { input: null, key: false, live: {}, fading: false, dipping: false }
		this.initActions()
		this.initFeedbacks()
		this.initVariables()
		this.initPresets()
		this.startPolling()
		this.updateStatus(InstanceStatus.Connecting)
	}

	async configUpdated(config) {
		this.config = config
		this.startPolling()
	}

	async destroy() {
		if (this.pollTimer) clearInterval(this.pollTimer)
	}

	getConfigFields() {
		return [
			{
				type: 'textinput',
				id: 'baseurl',
				label: 'Switcher base URL',
				width: 8,
				default: 'https://prodbots.com',
			},
			{
				type: 'number',
				id: 'pollms',
				label: 'Tally poll interval (ms)',
				width: 4,
				default: 1000,
				min: 250,
				max: 5000,
			},
		]
	}

	api(path, body) {
		const url = `${(this.config.baseurl || '').replace(/\/$/, '')}${path}`
		return fetch(url, {
			method: body !== undefined ? 'POST' : 'GET',
			headers: { 'Content-Type': 'application/json' },
			body: body !== undefined ? JSON.stringify(body) : undefined,
			signal: AbortSignal.timeout(8000),
		}).then((r) => r.json())
	}

	startPolling() {
		if (this.pollTimer) clearInterval(this.pollTimer)
		const tick = async () => {
			try {
				const st = await this.api('/api/mxl/status')
				if (st && typeof st.input !== 'undefined') {
					this.state.input = st.input
					this.state.key = !!st.key
					this.state.fading = !!st.fading
					this.state.dipping = !!st.dipping
					const live = {}
					for (const s of st.slots || []) live[s.slot] = s.live
					this.state.live = live
					this.updateStatus(InstanceStatus.Ok)
					const cur = SLOTS.find((s) => s.id === st.input)
					this.setVariableValues({
						pgm_slot: String(st.input),
						pgm_label: cur ? cur.label : '—',
						key_state: st.key ? 'ON' : 'OFF',
					})
					this.checkFeedbacks('pgm_tally', 'source_live', 'key_on')
				}
			} catch (e) {
				this.updateStatus(InstanceStatus.ConnectionFailure, String(e).slice(0, 60))
			}
		}
		tick()
		this.pollTimer = setInterval(tick, this.config.pollms || 1000)
	}

	initActions() {
		this.setActionDefinitions({
			cut: {
				name: 'Cut input to program',
				options: [{ type: 'dropdown', id: 'slot', label: 'Input', default: 0, choices: SLOT_CHOICES }],
				callback: async (a) => {
					await this.api('/api/mxl/input', { input: Number(a.options.slot) })
				},
			},
			key: {
				name: 'Keyer (graphics)',
				options: [
					{
						type: 'dropdown',
						id: 'mode',
						label: 'Mode',
						default: 'toggle',
						choices: [
							{ id: 'on', label: 'On' },
							{ id: 'off', label: 'Off' },
							{ id: 'toggle', label: 'Toggle' },
						],
					},
				],
				callback: async (a) => {
					const on = a.options.mode === 'toggle' ? !this.state.key : a.options.mode === 'on'
					await this.api('/api/mxl/key', { on })
				},
			},
			warmup: {
				name: 'Warm-up sweep (line-up all inputs)',
				options: [],
				callback: async () => {
					await this.api('/api/mxl/warmup', {})
				},
			},
			layout: {
				name: 'SuperSource layout',
				options: [
					{
						type: 'dropdown',
						id: 'style',
						label: 'Style',
						default: '2up',
						choices: [
							{ id: '2up', label: '2-UP' },
							{ id: 'pip', label: 'PiP' },
							{ id: '4up', label: '4-UP' },
						],
					},
					{ type: 'dropdown', id: 'a', label: 'Box A', default: 'cam', choices: SRC_CHOICES },
					{ type: 'dropdown', id: 'b', label: 'Box B', default: 'cam2', choices: SRC_CHOICES },
					{ type: 'dropdown', id: 'c', label: 'Box C (4-UP)', default: 'playout', choices: SRC_CHOICES },
					{ type: 'dropdown', id: 'd', label: 'Box D (4-UP)', default: 'pattern', choices: SRC_CHOICES },
				],
				callback: async (a) => {
					const o = a.options
					await this.api('/api/mxl/layout', { style: o.style, a: o.a, b: o.b, c: o.c, d: o.d })
				},
			},
		})
	}

	initFeedbacks() {
		this.setFeedbackDefinitions({
			pgm_tally: {
				type: 'boolean',
				name: 'Program tally (source on air)',
				defaultStyle: { bgcolor: combineRgb(200, 0, 0), color: combineRgb(255, 255, 255) },
				options: [{ type: 'dropdown', id: 'slot', label: 'Input', default: 0, choices: SLOT_CHOICES }],
				callback: (fb) => this.state.input === Number(fb.options.slot),
			},
			source_live: {
				type: 'boolean',
				name: 'Source has NO feed (dim it)',
				defaultStyle: { bgcolor: combineRgb(20, 20, 20), color: combineRgb(90, 90, 90) },
				options: [{ type: 'dropdown', id: 'slot', label: 'Input', default: 4, choices: SLOT_CHOICES }],
				callback: (fb) => this.state.live[Number(fb.options.slot)] === false,
			},
			key_on: {
				type: 'boolean',
				name: 'Keyer is on',
				defaultStyle: { bgcolor: combineRgb(0, 160, 70), color: combineRgb(255, 255, 255) },
				options: [],
				callback: () => this.state.key,
			},
		})
	}

	initVariables() {
		this.setVariableDefinitions([
			{ variableId: 'pgm_slot', name: 'Program slot number' },
			{ variableId: 'pgm_label', name: 'Program source name' },
			{ variableId: 'key_state', name: 'Keyer state' },
		])
	}

	initPresets() {
		const presets = {}
		for (const s of SLOTS) {
			presets[`cut_${s.id}`] = {
				type: 'button',
				category: 'Cuts',
				name: `Cut ${s.label}`,
				style: { text: s.label, size: '14', color: combineRgb(255, 255, 255), bgcolor: combineRgb(26, 35, 56) },
				steps: [{ down: [{ actionId: 'cut', options: { slot: s.id } }], up: [] }],
				feedbacks: [
					{ feedbackId: 'pgm_tally', options: { slot: s.id }, style: { bgcolor: combineRgb(200, 0, 0) } },
					{ feedbackId: 'source_live', options: { slot: s.id }, style: { bgcolor: combineRgb(20, 20, 20), color: combineRgb(90, 90, 90) } },
				],
			}
		}
		presets['key_toggle'] = {
			type: 'button',
			category: 'Switcher',
			name: 'KEY toggle',
			style: { text: 'KEY', size: '14', color: combineRgb(255, 255, 255), bgcolor: combineRgb(26, 35, 56) },
			steps: [{ down: [{ actionId: 'key', options: { mode: 'toggle' } }], up: [] }],
			feedbacks: [{ feedbackId: 'key_on', options: {}, style: { bgcolor: combineRgb(0, 160, 70) } }],
		}
		presets['warmup'] = {
			type: 'button',
			category: 'Switcher',
			name: 'Warm-up',
			style: { text: 'WARM\\nUP', size: '14', color: combineRgb(16, 22, 35), bgcolor: combineRgb(255, 180, 0) },
			steps: [{ down: [{ actionId: 'warmup', options: {} }], up: [] }],
			feedbacks: [],
		}
		this.setPresetDefinitions(presets)
	}
}

runEntrypoint(MXLSwitcherInstance, [])
