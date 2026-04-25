import { describe, expect, it } from 'vitest';

import {
	applyCompletionTokenData,
	applyTokenExplorerDefaults,
	annotateMarkdownTokensForTokenExplorer,
	buildTokenBranchPayload,
	buildTokenExplorerRanges,
	splitTextByTokenRanges
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

	it('aligns exact token stream to content', () => {
		const ranges = buildTokenExplorerRanges('Hello world.', {
			tokens: [
				{ index: 0, text: 'Hello', alternatives: [{ rank: 0, text: 'Hello' }] },
				{ index: 1, text: ' world', alternatives: [{ rank: 0, text: ' world' }] },
				{ index: 2, text: '.', alternatives: [{ rank: 0, text: '.' }] }
			]
		});

		expect(ranges.map((range) => [range.start, range.end, range.tokenIndex])).toEqual([
			[0, 5, 0],
			[5, 11, 1],
			[11, 12, 2]
		]);
	});

	it('aligns trimmed edge tokens when rendered content is trimmed', () => {
		const ranges = buildTokenExplorerRanges('Hello', {
			tokens: [
				{ index: 0, text: ' Hello', alternatives: [{ rank: 0, text: ' Hello' }] },
				{ index: 1, text: '\n', alternatives: [{ rank: 0, text: '\n' }] }
			]
		});

		expect(ranges).toHaveLength(1);
		expect(ranges[0].start).toBe(0);
		expect(ranges[0].end).toBe(5);
		expect(ranges[0].tokenIndex).toBe(0);
	});

	it('skips unaligned tokens without breaking later prose alignment', () => {
		const ranges = buildTokenExplorerRanges('Alpha Beta Gamma', {
			tokens: [
				{ index: 0, text: 'Alpha', alternatives: [{ rank: 0, text: 'Alpha' }] },
				{ index: 1, text: ' MISSING', alternatives: [{ rank: 0, text: ' MISSING' }] },
				{ index: 2, text: ' Beta', alternatives: [{ rank: 0, text: ' Beta' }] },
				{ index: 3, text: ' Gamma', alternatives: [{ rank: 0, text: ' Gamma' }] }
			]
		});

		expect(ranges.map((range) => range.tokenIndex)).toEqual([0, 2, 3]);
		expect(ranges.map((range) => 'Alpha Beta Gamma'.slice(range.start, range.end))).toEqual([
			'Alpha',
			' Beta',
			' Gamma'
		]);
	});

	it('splits text segments by overlapping token ranges', () => {
		const ranges = buildTokenExplorerRanges('Hello world.', {
			tokens: [
				{ index: 0, text: 'Hello', alternatives: [{ rank: 0, text: 'Hello' }] },
				{ index: 1, text: ' world', alternatives: [{ rank: 0, text: ' world' }] },
				{ index: 2, text: '.', alternatives: [{ rank: 0, text: '.' }] }
			]
		});

		const parts = splitTextByTokenRanges('lo wor', 3, ranges);
		expect(parts.map((part) => ({ text: part.text, tokenIndex: part.range?.tokenIndex }))).toEqual([
			{ text: 'lo', tokenIndex: 0 },
			{ text: ' wor', tokenIndex: 1 }
		]);
	});

	it('annotates prose tokens while skipping code source ranges', () => {
		const content = 'Before\n\n```txt\ncode token\n```\n\nAfter';
		const ranges = buildTokenExplorerRanges(content, {
			tokens: [
				{ index: 0, text: 'Before', alternatives: [{ rank: 0, text: 'Before' }] },
				{ index: 1, text: '\n\n```txt\ncode token\n```\n\n', alternatives: [] },
				{ index: 2, text: 'After', alternatives: [{ rank: 0, text: 'After' }] }
			]
		});
		const markdownTokens: any[] = [
			{ type: 'paragraph', raw: 'Before', tokens: [{ type: 'text', raw: 'Before' }] },
			{ type: 'code', raw: '```txt\ncode token\n```', text: 'code token' },
			{ type: 'paragraph', raw: 'After', tokens: [{ type: 'text', raw: 'After' }] }
		];

		annotateMarkdownTokensForTokenExplorer(markdownTokens, content, ranges);

		expect(markdownTokens[0].tokens[0].tokenExplorerParts[0].range.tokenIndex).toBe(0);
		expect(markdownTokens[1].tokenExplorerParts).toBeUndefined();
		expect(markdownTokens[2].tokens[0].tokenExplorerParts[0].range.tokenIndex).toBe(2);
	});
});
