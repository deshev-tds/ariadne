export type TokenBranchRequest = {
	source_message_id: string;
	fork_index: number;
	alt_rank: number;
};

export type TokenExplorerAlternative = {
	rank: number;
	text: string;
	tokenId?: number | null;
	logprob?: number | null;
	prob?: number | null;
};

export type TokenExplorerToken = {
	index?: number;
	text: string;
	tokenId?: number | null;
	logprob?: number | null;
	prob?: number | null;
	alternatives?: TokenExplorerAlternative[];
};

export type TokenExplorerRange = {
	start: number;
	end: number;
	tokenIndex: number;
	token: TokenExplorerToken;
};

export type TokenExplorerTextPart = {
	text: string;
	range?: TokenExplorerRange;
	branchPrefix?: boolean;
};

type MarkdownTokenLike = Record<string, any>;

export const applyTokenExplorerDefaults = (
	params: Record<string, any>,
	tokenExplorerEnabled: boolean
) => {
	const nextParams = { ...(params ?? {}) };
	if (!tokenExplorerEnabled) {
		return nextParams;
	}

	if (nextParams.logprobs === undefined && nextParams.log_probs === undefined) {
		nextParams.logprobs = true;
	}
	if (nextParams.top_logprobs === undefined) {
		nextParams.top_logprobs = 10;
	}

	return nextParams;
};

export const buildTokenBranchPayload = (
	sourceMessageId: string,
	forkIndex: number,
	altRank: number
): TokenBranchRequest => ({
	source_message_id: sourceMessageId,
	fork_index: forkIndex,
	alt_rank: altRank
});

export const buildTokenBranchDisplayPrefix = (
	telemetry: Record<string, any> | null | undefined,
	forkIndex: number,
	altRank: number
) => {
	const tokens = Array.isArray(telemetry?.tokens) ? telemetry.tokens : [];
	if (forkIndex < 0 || forkIndex >= tokens.length) {
		return '';
	}

	const alternatives = Array.isArray(tokens[forkIndex]?.alternatives)
		? tokens[forkIndex].alternatives
		: [];
	if (altRank < 0 || altRank >= alternatives.length) {
		return '';
	}

	const prefix = tokens
		.slice(0, forkIndex)
		.map((token) => coerceTokenText(token?.text))
		.join('');

	return `${prefix}${coerceTokenText(alternatives[altRank]?.text)}`;
};

const needsTokenBranchDisplaySpacer = (prefix: string, continuation: string) => {
	if (!prefix || !continuation) {
		return false;
	}
	if (/\s$/u.test(prefix) || /^\s/u.test(continuation)) {
		return false;
	}

	return /[\p{L}\p{N}]$/u.test(prefix) && /^[\p{L}\p{N}]/u.test(continuation);
};

export const joinTokenBranchDisplayPrefix = (prefix: unknown, continuation: unknown) => {
	const prefixText = coerceTokenText(prefix);
	const continuationText = coerceTokenText(continuation);
	if (!prefixText || continuationText.startsWith(prefixText)) {
		return continuationText;
	}

	const spacer = needsTokenBranchDisplaySpacer(prefixText, continuationText) ? ' ' : '';
	return `${prefixText}${spacer}${continuationText}`;
};

export const applyCompletionTokenData = (
	message: Record<string, any>,
	data: Record<string, any>
) => {
	if (data?.tokenTelemetry) {
		message.tokenTelemetry = data.tokenTelemetry;
		delete message.tokenTelemetryUnavailable;
		delete message.tokenTelemetryUnavailableReason;
	}

	if (data?.tokenBranch) {
		message.tokenBranch = data.tokenBranch;
	}

	if (data?.tokenTelemetryUnavailable === true) {
		message.tokenTelemetryUnavailable = true;
		message.tokenTelemetryUnavailableReason =
			data.tokenTelemetryUnavailableReason ?? 'unsupported_logprobs';
	}

	return message;
};

const coerceTokenText = (value: unknown) => {
	if (typeof value === 'string') {
		return value;
	}
	if (value === undefined || value === null) {
		return '';
	}
	return String(value);
};

const findTokenText = (content: string, tokenText: string, cursor: number) => {
	if (!tokenText) {
		return null;
	}

	const exactIndex = content.indexOf(tokenText, cursor);
	if (exactIndex >= 0) {
		return { start: exactIndex, end: exactIndex + tokenText.length };
	}

	const trimmed = tokenText.trim();
	if (!trimmed || trimmed === tokenText) {
		return null;
	}

	const trimmedIndex = content.indexOf(trimmed, cursor);
	if (trimmedIndex >= 0) {
		return { start: trimmedIndex, end: trimmedIndex + trimmed.length };
	}

	return null;
};

export const buildTokenExplorerRanges = (
	content: string,
	telemetry: Record<string, any> | null | undefined
): TokenExplorerRange[] => {
	if (typeof content !== 'string' || !content) {
		return [];
	}

	const tokens = Array.isArray(telemetry?.tokens) ? telemetry.tokens : [];
	const ranges: TokenExplorerRange[] = [];
	let cursor = 0;

	for (const rawToken of tokens) {
		const tokenText = coerceTokenText(rawToken?.text);
		const match = findTokenText(content, tokenText, cursor);
		if (!match) {
			continue;
		}

		const tokenIndex = Number.isInteger(rawToken?.index) ? rawToken.index : ranges.length;
		const token: TokenExplorerToken = {
			...rawToken,
			index: tokenIndex,
			text: tokenText,
			alternatives: Array.isArray(rawToken?.alternatives) ? rawToken.alternatives : []
		};

		ranges.push({
			start: match.start,
			end: match.end,
			tokenIndex,
			token
		});
		cursor = match.end;
	}

	return ranges;
};

export const splitTextByTokenRanges = (
	text: string,
	absoluteStart: number,
	ranges: TokenExplorerRange[]
): TokenExplorerTextPart[] => {
	if (!text || !Array.isArray(ranges) || ranges.length === 0) {
		return [{ text }];
	}

	const absoluteEnd = absoluteStart + text.length;
	const parts: TokenExplorerTextPart[] = [];
	let cursor = absoluteStart;

	for (const range of ranges) {
		if (range.end <= cursor) {
			continue;
		}
		if (range.start >= absoluteEnd) {
			break;
		}

		const partStart = Math.max(range.start, absoluteStart);
		const partEnd = Math.min(range.end, absoluteEnd);
		if (partEnd <= partStart) {
			continue;
		}

		if (partStart > cursor) {
			parts.push({
				text: text.slice(cursor - absoluteStart, partStart - absoluteStart)
			});
		}

		parts.push({
			text: text.slice(partStart - absoluteStart, partEnd - absoluteStart),
			range
		});
		cursor = partEnd;
	}

	if (cursor < absoluteEnd) {
		parts.push({ text: text.slice(cursor - absoluteStart) });
	}

	return parts.length > 0 ? parts : [{ text }];
};

const markTextPartsForBranchPrefix = (
	parts: TokenExplorerTextPart[],
	absoluteStart: number,
	prefixLength: number
): TokenExplorerTextPart[] => {
	if (!Array.isArray(parts) || parts.length === 0 || prefixLength <= absoluteStart) {
		return parts;
	}

	const markedParts: TokenExplorerTextPart[] = [];
	let cursor = absoluteStart;

	for (const part of parts) {
		const text = coerceTokenText(part?.text);
		const partStart = cursor;
		const partEnd = partStart + text.length;
		cursor = partEnd;

		if (!text) {
			markedParts.push(part);
			continue;
		}

		if (partStart >= prefixLength) {
			markedParts.push(part);
			continue;
		}

		if (partEnd <= prefixLength) {
			markedParts.push({ ...part, branchPrefix: true });
			continue;
		}

		const splitAt = prefixLength - partStart;
		markedParts.push({
			...part,
			text: text.slice(0, splitAt),
			branchPrefix: true
		});
		markedParts.push({
			...part,
			text: text.slice(splitAt),
			branchPrefix: false
		});
	}

	return markedParts;
};

export const formatTokenExplorerProbability = (probability: unknown) => {
	const value = Number(probability);
	if (!Number.isFinite(value) || value < 0) {
		return 'n/a';
	}
	if (value >= 0.1) {
		return `${(value * 100).toFixed(1)}%`;
	}
	if (value >= 0.001) {
		return `${(value * 100).toFixed(3)}%`;
	}
	return `${(value * 100).toExponential(1)}%`;
};

export const formatTokenExplorerLogprob = (logprob: unknown) => {
	const value = Number(logprob);
	if (!Number.isFinite(value)) {
		return 'n/a';
	}
	return value.toFixed(3);
};

const TOKEN_EXPLORER_NON_PROSE_TOKEN_TYPES = new Set([
	'code',
	'codespan',
	'html',
	'iframe',
	'image',
	'inlineKatex',
	'footnote',
	'citation'
]);

const findSegmentBounds = (content: string, segment: unknown, cursor: number) => {
	if (typeof segment !== 'string' || segment.length === 0) {
		return null;
	}

	const index = content.indexOf(segment, cursor);
	if (index < 0) {
		return null;
	}

	return { start: index, end: index + segment.length };
};

const advancePastTokenSource = (
	token: MarkdownTokenLike,
	content: string,
	state: { cursor: number }
) => {
	const bounds = findSegmentBounds(content, token.raw ?? token.text, state.cursor);
	if (bounds) {
		state.cursor = bounds.end;
	}
};

const annotateTokenArray = (
	tokens: MarkdownTokenLike[],
	content: string,
	ranges: TokenExplorerRange[],
	state: { cursor: number }
) => {
	for (const token of tokens ?? []) {
		annotateToken(token, content, ranges, state);
	}
};

const annotateTableCells = (
	cells: MarkdownTokenLike[],
	content: string,
	ranges: TokenExplorerRange[],
	state: { cursor: number }
) => {
	for (const cell of cells ?? []) {
		if (Array.isArray(cell?.tokens)) {
			annotateTokenArray(cell.tokens, content, ranges, state);
		}
	}
};

const annotateTokenChildren = (
	token: MarkdownTokenLike,
	content: string,
	ranges: TokenExplorerRange[],
	state: { cursor: number }
) => {
	if (Array.isArray(token.tokens)) {
		annotateTokenArray(token.tokens, content, ranges, state);
	}

	if (Array.isArray(token.items)) {
		for (const item of token.items) {
			if (Array.isArray(item?.tokens)) {
				annotateTokenArray(item.tokens, content, ranges, state);
			}
		}
	}

	if (Array.isArray(token.header)) {
		annotateTableCells(token.header, content, ranges, state);
	}

	if (Array.isArray(token.rows)) {
		for (const row of token.rows) {
			annotateTableCells(row, content, ranges, state);
		}
	}
};

const annotateToken = (
	token: MarkdownTokenLike,
	content: string,
	ranges: TokenExplorerRange[],
	state: { cursor: number }
) => {
	if (!token || typeof token !== 'object') {
		return;
	}

	const rawBounds = findSegmentBounds(content, token.raw, state.cursor);
	if (rawBounds) {
		state.cursor = rawBounds.start;
	}

	if (TOKEN_EXPLORER_NON_PROSE_TOKEN_TYPES.has(token.type)) {
		advancePastTokenSource(token, content, state);
		if (rawBounds) {
			state.cursor = Math.max(state.cursor, rawBounds.end);
		}
		return;
	}

	if (token.type === 'text' && typeof token.raw === 'string') {
		const bounds = findSegmentBounds(content, token.raw, state.cursor);
		if (bounds) {
			token.tokenExplorerParts = splitTextByTokenRanges(token.raw, bounds.start, ranges);
			state.cursor = bounds.end;
		}
		if (rawBounds) {
			state.cursor = Math.max(state.cursor, rawBounds.end);
		}
		return;
	}

	annotateTokenChildren(token, content, ranges, state);

	if (rawBounds) {
		state.cursor = Math.max(state.cursor, rawBounds.end);
	}
};

export const annotateMarkdownTokensForTokenExplorer = (
	tokens: MarkdownTokenLike[],
	content: string,
	ranges: TokenExplorerRange[]
) => {
	if (!Array.isArray(tokens) || !content || !Array.isArray(ranges) || ranges.length === 0) {
		return tokens;
	}

	annotateTokenArray(tokens, content, ranges, { cursor: 0 });
	return tokens;
};

const annotateTokenArrayForBranchPrefix = (
	tokens: MarkdownTokenLike[],
	content: string,
	prefixLength: number,
	state: { cursor: number }
) => {
	for (const token of tokens ?? []) {
		annotateTokenForBranchPrefix(token, content, prefixLength, state);
	}
};

const annotateTableCellsForBranchPrefix = (
	cells: MarkdownTokenLike[],
	content: string,
	prefixLength: number,
	state: { cursor: number }
) => {
	for (const cell of cells ?? []) {
		if (Array.isArray(cell?.tokens)) {
			annotateTokenArrayForBranchPrefix(cell.tokens, content, prefixLength, state);
		}
	}
};

const annotateTokenChildrenForBranchPrefix = (
	token: MarkdownTokenLike,
	content: string,
	prefixLength: number,
	state: { cursor: number }
) => {
	if (Array.isArray(token.tokens)) {
		annotateTokenArrayForBranchPrefix(token.tokens, content, prefixLength, state);
	}

	if (Array.isArray(token.items)) {
		for (const item of token.items) {
			if (Array.isArray(item?.tokens)) {
				annotateTokenArrayForBranchPrefix(item.tokens, content, prefixLength, state);
			}
		}
	}

	if (Array.isArray(token.header)) {
		annotateTableCellsForBranchPrefix(token.header, content, prefixLength, state);
	}

	if (Array.isArray(token.rows)) {
		for (const row of token.rows) {
			annotateTableCellsForBranchPrefix(row, content, prefixLength, state);
		}
	}
};

const annotateTokenForBranchPrefix = (
	token: MarkdownTokenLike,
	content: string,
	prefixLength: number,
	state: { cursor: number }
) => {
	if (!token || typeof token !== 'object' || state.cursor >= prefixLength) {
		return;
	}

	const rawBounds = findSegmentBounds(content, token.raw, state.cursor);
	if (rawBounds) {
		state.cursor = rawBounds.start;
	}

	if (TOKEN_EXPLORER_NON_PROSE_TOKEN_TYPES.has(token.type)) {
		advancePastTokenSource(token, content, state);
		if (rawBounds) {
			state.cursor = Math.max(state.cursor, rawBounds.end);
		}
		return;
	}

	if (token.type === 'text' && typeof token.raw === 'string') {
		const bounds = findSegmentBounds(content, token.raw, state.cursor);
		if (bounds) {
			const parts = Array.isArray(token.tokenExplorerParts)
				? token.tokenExplorerParts
				: [{ text: token.raw }];
			token.tokenExplorerParts = markTextPartsForBranchPrefix(
				parts,
				bounds.start,
				prefixLength
			);
			state.cursor = bounds.end;
		}
		if (rawBounds) {
			state.cursor = Math.max(state.cursor, rawBounds.end);
		}
		return;
	}

	annotateTokenChildrenForBranchPrefix(token, content, prefixLength, state);

	if (rawBounds) {
		state.cursor = Math.max(state.cursor, rawBounds.end);
	}
};

export const annotateMarkdownTokensForTokenBranchPrefix = (
	tokens: MarkdownTokenLike[],
	content: string,
	prefixLength: number
) => {
	if (!Array.isArray(tokens) || !content || !Number.isFinite(prefixLength) || prefixLength <= 0) {
		return tokens;
	}

	annotateTokenArrayForBranchPrefix(tokens, content, Math.min(prefixLength, content.length), {
		cursor: 0
	});
	return tokens;
};
