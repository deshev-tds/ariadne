import { describe, expect, it } from 'vitest';

import { resolveScienceLaneTerminalId } from './scienceLane';

describe('science lane helpers', () => {
	it('preserves an explicitly selected terminal', () => {
		expect(
			resolveScienceLaneTerminalId({
				selectedTerminalId: 'system-terminal',
				systemTerminals: [{ id: 'fallback-system' }],
				directTerminals: [{ url: 'https://terminal.example', enabled: true }]
			})
		).toBe('system-terminal');
	});

	it('prefers the first system terminal for science mode auto-attach', () => {
		expect(
			resolveScienceLaneTerminalId({
				selectedTerminalId: null,
				systemTerminals: [{ id: 'system-a' }, { id: 'system-b' }],
				directTerminals: [{ url: 'https://terminal.example', enabled: true }]
			})
		).toBe('system-a');
	});

	it('falls back to an already enabled direct terminal when no system terminal exists', () => {
		expect(
			resolveScienceLaneTerminalId({
				selectedTerminalId: null,
				systemTerminals: [],
				directTerminals: [
					{ url: 'https://disabled.example', enabled: false },
					{ url: 'https://enabled.example', enabled: true }
				]
			})
		).toBe('https://enabled.example');
	});

	it('returns null when no terminal can be auto-selected', () => {
		expect(
			resolveScienceLaneTerminalId({
				selectedTerminalId: null,
				systemTerminals: [],
				directTerminals: [{ url: 'https://disabled.example', enabled: false }]
			})
		).toBeNull();
	});
});
