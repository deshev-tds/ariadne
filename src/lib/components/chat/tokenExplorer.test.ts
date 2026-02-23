import { describe, expect, it } from 'vitest';

import {
	applyCompletionTokenData,
	applyTokenExplorerDefaults,
	buildTokenBranchPayload
} from './tokenExplorer';

describe('tokenExplorer helpers', () => {
	it('adds logprobs defaults when enabled', () => {
		const result = applyTokenExplorerDefaults({ temperature: 0.2 }, true);
		expect(result.logprobs).toBe(true);
		expect(result.top_logprobs).toBe(10);
	});

	it('preserves explicit logprobs overrides', () => {
		const result = applyTokenExplorerDefaults(
			{
				logprobs: false,
				top_logprobs: 2
			},
			true
		);

		expect(result.logprobs).toBe(false);
		expect(result.top_logprobs).toBe(2);
	});

	it('builds branch payload', () => {
		expect(buildTokenBranchPayload('assistant-msg-id', 42, 1)).toEqual({
			source_message_id: 'assistant-msg-id',
			fork_index: 42,
			alt_rank: 1
		});
	});

	it('applies completion token data to a message', () => {
		const message: Record<string, any> = {
			tokenTelemetryUnavailable: true,
			tokenTelemetryUnavailableReason: 'unsupported_logprobs'
		};

		applyCompletionTokenData(message, {
			tokenTelemetry: { version: 1, tokens: [] },
			tokenBranch: { version: 1, forkIndex: 3 }
		});

		expect(message.tokenTelemetry).toEqual({ version: 1, tokens: [] });
		expect(message.tokenBranch).toEqual({ version: 1, forkIndex: 3 });
		expect(message.tokenTelemetryUnavailable).toBeUndefined();
		expect(message.tokenTelemetryUnavailableReason).toBeUndefined();
	});
});
