import { describe, expect, it } from 'vitest';

import { materializeOutputForDisplay } from './runtimeDetails';

describe('runtimeDetails', () => {
	it('rebuilds reasoning and tool call details from output for display', () => {
		const rendered = materializeOutputForDisplay('Visible answer', [
			{
				type: 'reasoning',
				status: 'completed',
				duration: 2,
				content: [{ type: 'output_text', text: 'internal chain' }]
			},
			{
				type: 'function_call',
				call_id: 'call-1',
				name: 'search_web',
				arguments: '{"q":"blue light glasses"}'
			},
			{
				type: 'function_call_output',
				call_id: 'call-1',
				output: [{ type: 'input_text', text: 'tool result body' }]
			},
			{
				type: 'message',
				content: [{ type: 'output_text', text: 'Visible answer' }]
			}
		]);

		expect(rendered).toContain('<details type="reasoning" done="true" duration="2">');
		expect(rendered).toContain('<details type="tool_calls" done="true" id="call-1"');
		expect(rendered).toContain('Visible answer');
	});

	it('does not duplicate details when content already contains them', () => {
		const content =
			'<details type="reasoning" done="true"><summary>Thought</summary>\n&gt; internal\n</details>\n\nVisible answer';

		expect(
			materializeOutputForDisplay(content, [
				{
					type: 'reasoning',
					status: 'completed',
					content: [{ type: 'output_text', text: 'internal' }]
				}
			])
		).toBe(content);
	});

	it('keeps current visible content when it differs from raw output text', () => {
		const rendered = materializeOutputForDisplay('Normalized visible answer', [
			{
				type: 'reasoning',
				status: 'completed',
				content: [{ type: 'output_text', text: 'internal chain' }]
			},
			{
				type: 'message',
				content: [{ type: 'output_text', text: 'Original visible answer' }]
			}
		]);

		expect(rendered).toContain('<details type="reasoning" done="true"');
		expect(rendered.endsWith('Normalized visible answer')).toBe(true);
	});
});
