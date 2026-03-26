<script lang="ts">
	import type { ContextWindowPreview } from '$lib/apis/chats';

	import Tooltip from '../common/Tooltip.svelte';
	import {
		buildContextWindowMetrics,
		clampRatio,
		formatTokenCount,
		getConfidenceLabel
	} from './contextWindow';

	export let preview: ContextWindowPreview | null = null;
	export let draftPrompt = '';

	const radius = 16;
	const circumference = 2 * Math.PI * radius;

	const arcStyle = (startRatio: number, endRatio: number) => {
		const start = clampRatio(startRatio);
		const end = clampRatio(endRatio);
		const length = Math.max(0, end - start) * circumference;

		return `stroke-dasharray: ${length} ${circumference}; stroke-dashoffset: ${
			circumference * (1 - start)
		};`;
	};

	$: metrics = buildContextWindowMetrics(preview, draftPrompt);

	$: progressStroke =
		preview?.hard_trigger_tokens != null && metrics.currentTokens >= preview.hard_trigger_tokens
			? '#f43f5e'
			: preview?.soft_trigger_tokens != null && metrics.currentTokens >= preview.soft_trigger_tokens
				? '#f59e0b'
				: '#10b981';

	$: bandStroke = metrics.degraded ? 'rgba(251, 191, 36, 0.35)' : 'rgba(251, 191, 36, 0.7)';
	$: ghostStroke = metrics.degraded ? 'rgba(56, 189, 248, 0.45)' : 'rgba(56, 189, 248, 0.7)';

	$: tooltipContent = preview
		? `
			<div class="max-w-[220px] space-y-1.5 px-1 py-0.5 text-xs leading-5">
				<div class="font-medium">Context window</div>
				<div>${formatTokenCount(metrics.currentTokens)} / ${formatTokenCount(metrics.livePromptCap)} tokens</div>
				<div>${Math.round(metrics.currentRatio * 100)}% used</div>
				${
					preview.maintenance_enabled && metrics.showBand
						? `<div>${
								metrics.degraded ? 'Approximate maintenance range' : 'Maintenance likely around'
						  } ~${formatTokenCount(metrics.softTriggerTokens ?? 0)}-${formatTokenCount(
								metrics.hardTriggerTokens ?? 0
						  )}</div>`
						: `<div>Context maintenance off</div>`
				}
				${preview.multi_model ? `<div>Limited by ${preview.limiting_model_name}</div>` : ''}
				${preview.summary_active ? `<div>Context snapshot active</div>` : ''}
				<div>${getConfidenceLabel(preview.token_count_confidence)}</div>
			</div>
		`
		: '';
</script>

{#if preview}
	<Tooltip content={tooltipContent} placement="bottom" offset={[0, 8]} interactive={true}>
		<button
			class="relative ml-1.5 flex size-9 items-center justify-center rounded-xl transition hover:bg-gray-50 dark:hover:bg-gray-850"
			aria-label="Context window"
		>
			<svg viewBox="0 0 40 40" class="-rotate-90 size-7">
				<circle
					cx="20"
					cy="20"
					r={radius}
					fill="none"
					stroke-width="4"
					class="stroke-gray-200 dark:stroke-gray-700"
				/>

				{#if preview.maintenance_enabled && metrics.showBand}
					<circle
						cx="20"
						cy="20"
					r={radius}
					fill="none"
					stroke-width="4"
					stroke-linecap="round"
					style={`${arcStyle(metrics.softRatio, metrics.hardRatio)} stroke: ${bandStroke};`}
				/>
			{/if}

				<circle
					cx="20"
					cy="20"
					r={radius}
					fill="none"
					stroke-width="4"
					stroke-linecap="round"
					style={`${arcStyle(0, metrics.currentRatio)} stroke: ${progressStroke};`}
				/>

				{#if metrics.ghostRatio > metrics.currentRatio}
					<circle
						cx="20"
						cy="20"
						r={radius}
						fill="none"
						stroke-width="2.5"
						stroke-linecap="round"
						style={`${arcStyle(metrics.currentRatio, metrics.ghostRatio)} stroke: ${ghostStroke};`}
					/>
				{/if}
			</svg>

			<div class="absolute text-[10px] font-medium text-gray-700 dark:text-gray-300">
				{Math.round(metrics.currentRatio * 100)}
			</div>

			{#if metrics.degraded}
				<div
					class="absolute -bottom-0.5 -right-0.5 rounded-full bg-amber-500 px-1 text-[9px] leading-3 text-white"
				>
					~
				</div>
			{/if}
		</button>
	</Tooltip>
{/if}
