const escapeHtml = (value: string) =>
	value
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;');

const stringifyAttributeValue = (value: unknown) => JSON.stringify(value ?? '');

const extractTextFromBlocks = (blocks: unknown) => {
	if (!Array.isArray(blocks)) {
		return '';
	}

	return blocks
		.map((block) => {
			if (!block || typeof block !== 'object' || !('text' in block)) {
				return '';
			}

			const text = (block as { text?: unknown }).text;
			return text == null ? '' : String(text);
		})
		.join('');
};

export const stripRuntimeDetailBlocks = (content: string) =>
	content
		.replace(/<details\b(?=[^>]*\btype="reasoning")[^>]*>[\s\S]*?<\/details>\s*/gi, '')
		.replace(/<details\b(?=[^>]*\btype="tool_calls")[^>]*>[\s\S]*?<\/details>\s*/gi, '')
		.replace(/<details\b(?=[^>]*\btype="code_interpreter")[^>]*>[\s\S]*?<\/details>\s*/gi, '')
		.replace(/<\/?think>/gi, '')
		.replace(/\n{3,}/g, '\n\n')
		.trim();

export const serializeOutputForDisplay = (output: unknown) => {
	if (!Array.isArray(output)) {
		return '';
	}

	let content = '';
	const toolOutputs = new Map<string, Record<string, unknown>>();

	for (const item of output) {
		if (!item || typeof item !== 'object') {
			continue;
		}

		const typedItem = item as Record<string, unknown>;
		if (typedItem.type === 'function_call_output') {
			toolOutputs.set(String(typedItem.call_id ?? ''), typedItem);
		}
	}

	for (const [idx, item] of output.entries()) {
		if (!item || typeof item !== 'object') {
			continue;
		}

		const typedItem = item as Record<string, unknown>;
		const itemType = String(typedItem.type ?? '');

		if (itemType === 'message') {
			const contentParts = Array.isArray(typedItem.content) ? typedItem.content : [];
			for (const contentPart of contentParts) {
				if (!contentPart || typeof contentPart !== 'object' || !('text' in contentPart)) {
					continue;
				}

				const text = String((contentPart as { text?: unknown }).text ?? '').trim();
				if (text) {
					content = `${content}${text}\n`;
				}
			}
			continue;
		}

		if (itemType === 'function_call') {
			if (content && !content.endsWith('\n')) {
				content += '\n';
			}

			const callId = String(typedItem.call_id ?? '');
			const name = String(typedItem.name ?? '');
			const argumentsValue = typedItem.arguments ?? '';
			const resultItem = toolOutputs.get(callId);

			if (resultItem) {
				const resultText = extractTextFromBlocks(resultItem.output);
				const files = resultItem.files ?? '';
				const embeds = resultItem.embeds ?? '';

				content += `<details type="tool_calls" done="true" id="${escapeHtml(callId)}" name="${escapeHtml(name)}" arguments="${escapeHtml(stringifyAttributeValue(argumentsValue))}" result="${escapeHtml(stringifyAttributeValue(resultText))}" files="${files ? escapeHtml(JSON.stringify(files)) : ''}" embeds="${escapeHtml(JSON.stringify(embeds))}">\n<summary>Tool Executed</summary>\n</details>\n`;
			} else {
				content += `<details type="tool_calls" done="false" id="${escapeHtml(callId)}" name="${escapeHtml(name)}" arguments="${escapeHtml(stringifyAttributeValue(argumentsValue))}">\n<summary>Executing...</summary>\n</details>\n`;
			}

			continue;
		}

		if (itemType === 'reasoning') {
			const reasoningContent = extractTextFromBlocks(typedItem.summary ?? typedItem.content).trim();
			const duration = typedItem.duration;
			const status = String(typedItem.status ?? 'in_progress');
			const isLastItem = idx === output.length - 1;

			if (content && !content.endsWith('\n')) {
				content += '\n';
			}

			const display = escapeHtml(
				reasoningContent
					.split('\n')
					.map((line) => (line.startsWith('>') ? line : `> ${line}`))
					.join('\n')
			);

			if (status === 'completed' || duration != null || !isLastItem) {
				content += `<details type="reasoning" done="true" duration="${duration || 0}">\n<summary>Thought for ${duration || 0} seconds</summary>\n${display}\n</details>\n`;
			} else {
				content += `<details type="reasoning" done="false">\n<summary>Thinking...</summary>\n${display}\n</details>\n`;
			}

			continue;
		}

		if (itemType === 'open_webui:code_interpreter') {
			if (content && !content.endsWith('\n')) {
				content += '\n';
			}

			const code = String(typedItem.code ?? '').trim();
			const lang = String(typedItem.lang ?? 'python');
			const status = String(typedItem.status ?? 'in_progress');
			const duration = typedItem.duration;
			const isLastItem = idx === output.length - 1;
			const ciOutput = typedItem.output;

			const display = code ? `\`\`\`${lang}\n${code}\n\`\`\`` : '';
			const outputAttr = ciOutput
				? ` output="${escapeHtml(
						JSON.stringify(
							typeof ciOutput === 'object' && ciOutput !== null
								? ciOutput
								: { result: String(ciOutput) }
						)
					)}"`
				: '';

			if (status === 'completed' || duration != null || !isLastItem) {
				content += `<details type="code_interpreter" done="true" duration="${duration || 0}"${outputAttr}>\n<summary>Analyzed</summary>\n${display}\n</details>\n`;
			} else {
				content += `<details type="code_interpreter" done="false"${outputAttr}>\n<summary>Analyzing...</summary>\n${display}\n</details>\n`;
			}
		}
	}

	return content.trim();
};

export const materializeOutputForDisplay = (content: string, output?: unknown) => {
	if (typeof content !== 'string' || !Array.isArray(output) || output.length === 0) {
		return content;
	}

	if (/<details/i.test(content)) {
		return content;
	}

	const materialized = serializeOutputForDisplay(output);
	if (!/<details/i.test(materialized)) {
		return content;
	}

	if (stripRuntimeDetailBlocks(materialized) === content.trim()) {
		return materialized;
	}

	const detailBlocks = materialized.match(/<details\b[\s\S]*?<\/details>/gi) ?? [];
	if (detailBlocks.length === 0) {
		return content;
	}

	const detailsPrefix = detailBlocks
		.map((block) => block.trim())
		.join('\n\n')
		.trim();
	if (!detailsPrefix) {
		return content;
	}

	const visibleContent = content.trim();
	return visibleContent ? `${detailsPrefix}\n\n${visibleContent}` : detailsPrefix;
};
