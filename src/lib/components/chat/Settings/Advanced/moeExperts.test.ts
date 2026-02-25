import { describe, expect, it } from 'vitest';

import {
	getMoeExpertsControlState,
	withMoeExpertsLevel,
	type MoeExpertsProbe
} from './moeExperts';

const buildModel = (enabled: boolean) => ({
	id: 'demo-model',
	info: {
		meta: {
			capabilities: {
				moe_experts_control: enabled
			}
		}
	}
});

describe('moeExperts helpers', () => {
	it('hides control when capability is disabled', () => {
		const state = getMoeExpertsControlState({
			models: [buildModel(false)],
			probe: null,
			loading: false
		});

		expect(state.visible).toBe(false);
		expect(state.enabled).toBe(false);
	});

	it('enables control when capability is on and probe succeeds', () => {
		const probe: MoeExpertsProbe = {
			supported: true,
			reason: null,
			model_id: 'demo-model',
			current: 8,
			default: 8,
			presets: { few: 4, default: 8, many: 12, a_lot: 16 }
		};

		const state = getMoeExpertsControlState({
			models: [buildModel(true)],
			probe,
			loading: false
		});

		expect(state.visible).toBe(true);
		expect(state.enabled).toBe(true);
		expect(state.reason).toBeNull();
	});

	it('shows disabled control in multi-model mode', () => {
		const state = getMoeExpertsControlState({
			models: [buildModel(true), buildModel(true)],
			probe: null,
			loading: false
		});

		expect(state.visible).toBe(true);
		expect(state.enabled).toBe(false);
		expect(state.reason).toBe('single model required');
	});

	it('shows disabled control for arena selection', () => {
		const state = getMoeExpertsControlState({
			models: [{ ...buildModel(true), arena: true }],
			probe: null,
			loading: false
		});

		expect(state.visible).toBe(true);
		expect(state.enabled).toBe(false);
		expect(state.reason).toBe('single model required');
	});

	it('disables control with probe failure reason', () => {
		const probe: MoeExpertsProbe = {
			supported: false,
			reason: 'Probe timed out',
			model_id: 'demo-model'
		};

		const state = getMoeExpertsControlState({
			models: [buildModel(true)],
			probe,
			loading: false
		});

		expect(state.visible).toBe(true);
		expect(state.enabled).toBe(false);
		expect(state.reason).toBe('Probe timed out');
	});

	it('stores label in params when selection changes', () => {
		const params = withMoeExpertsLevel({ temperature: 0.2 }, 'many');
		expect(params.moe_experts_level).toBe('many');
		expect(params.temperature).toBe(0.2);
	});
});
