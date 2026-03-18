<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount, getContext } from 'svelte';
	import {
		clearRuntimeTelemetry,
		getRuntimeTelemetry,
		startRuntimeTelemetry,
		stopRuntimeTelemetry
	} from '$lib/apis/analytics';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	type RuntimeTelemetryEvent = {
		seq: number;
		ts: number;
		kind: string;
		chat_id?: string | null;
		message_id?: string | null;
		user_id?: string | null;
		model_id?: string | null;
		payload?: Record<string, any>;
	};

	type RuntimeTelemetryMessageSummary = {
		chat_id: string;
		message_id: string;
		user_id?: string | null;
		first_seen_at: number;
		last_seen_at: number;
		event_count: number;
		tool_event_count: number;
		model_activity_count: number;
		fallback_count: number;
		models?: string[];
		active_models?: string[];
		task_kinds?: string[];
		operations?: string[];
		memory?: Record<string, any> | null;
		prompt_entry_count?: number;
	};

	type RuntimeTelemetrySnapshot = {
		enabled: boolean;
		started_at?: number | null;
		buffer_size: number;
		message_buffer_size: number;
		total_events: number;
		kind_counts: Record<string, number>;
		tool_journey_count: number;
		model_activity_count: number;
		fallback_count: number;
		recent_events: RuntimeTelemetryEvent[];
		recent_messages: RuntimeTelemetryMessageSummary[];
	};

	let telemetry: RuntimeTelemetrySnapshot | null = null;
	let loading = true;
	let actionLoading = false;
	let autoRefresh = true;
	let lastError = '';
	let mounted = false;
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	const pollIntervalMs = 2000;
	const eventLimit = 120;
	$: telemetryEnabled = Boolean(telemetry?.enabled);

	const formatTs = (value?: number | null) => {
		if (!value) return '—';
		return new Date(value * 1000).toLocaleTimeString();
	};

	const formatDateTime = (value?: number | null) => {
		if (!value) return '—';
		return new Date(value * 1000).toLocaleString();
	};

	const compactId = (value?: string | null, size: number = 10) => {
		if (!value) return '—';
		return value.length > size ? `${value.slice(0, size)}…` : value;
	};

	const formatList = (values?: string[] | null) => {
		if (!values?.length) return '—';
		return values.join(', ');
	};

	const eventSummary = (event: RuntimeTelemetryEvent) => {
		const payload = event.payload ?? {};
		const bits = [
			payload.phase,
			payload.operation,
			payload.task_kind,
			payload.tool,
			payload.status,
			payload.error_class
		].filter(Boolean);
		return bits.length ? bits.join(' · ') : event.kind;
	};

	const eventMeta = (event: RuntimeTelemetryEvent) => {
		const payload = event.payload ?? {};
		const bits = [
			payload.actor,
			payload.model_id,
			payload.active_model_id,
			payload.selected_via,
			payload.route_source
		].filter(Boolean);
		return bits.length ? bits.join(' · ') : '—';
	};

	const loadTelemetry = async () => {
		try {
			lastError = '';
			telemetry = await getRuntimeTelemetry(localStorage.token, eventLimit);
		} catch (error) {
			console.error('Runtime telemetry load failed:', error);
			lastError = `${error}`;
		} finally {
			loading = false;
		}
	};

	const runAction = async (action: 'start' | 'stop' | 'clear') => {
		actionLoading = true;
		try {
			lastError = '';
			if (action === 'start') {
				telemetry = await startRuntimeTelemetry(localStorage.token);
			} else if (action === 'stop') {
				telemetry = await stopRuntimeTelemetry(localStorage.token);
			} else {
				telemetry = await clearRuntimeTelemetry(localStorage.token);
			}
		} catch (error) {
			console.error(`Runtime telemetry ${action} failed:`, error);
			lastError = `${error}`;
		} finally {
			actionLoading = false;
		}
	};

	const refreshNow = async () => {
		loading = true;
		await loadTelemetry();
	};

	const syncPolling = (enabled: boolean) => {
		if (pollHandle) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
		if (enabled) {
			pollHandle = setInterval(() => {
				loadTelemetry();
			}, pollIntervalMs);
		}
	};

	$: if (browser && mounted) {
		syncPolling(autoRefresh);
	}

	onMount(async () => {
		mounted = true;
		await loadTelemetry();
	});

	onDestroy(() => {
		mounted = false;
		if (pollHandle) {
			clearInterval(pollHandle);
		}
	});
</script>

<div class="flex flex-col gap-4">
	<div
		class="flex flex-col gap-3 rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
	>
		<div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
			<div>
				<div class="text-lg font-medium text-gray-900 dark:text-white">
					{$i18n.t('Runtime Telemetry')}
				</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'Live in-memory view of tool journey, prompt, memory and model activity telemetry.'
					)}
				</div>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<label
					class="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300"
				>
					<input bind:checked={autoRefresh} type="checkbox" />
					<span>{$i18n.t('Auto refresh')}</span>
				</label>
				<button
					class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
					disabled={loading || actionLoading}
					on:click={refreshNow}
				>
					{$i18n.t('Refresh')}
				</button>
				<button
					class="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
					disabled={actionLoading || telemetryEnabled}
					on:click={() => runAction('start')}
				>
					{$i18n.t('Start')}
				</button>
				<button
					class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
					disabled={actionLoading || !telemetryEnabled}
					on:click={() => runAction('stop')}
				>
					{$i18n.t('Stop')}
				</button>
				<button
					class="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-50 disabled:opacity-60 dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/40"
					disabled={actionLoading}
					on:click={() => runAction('clear')}
				>
					{$i18n.t('Clear')}
				</button>
			</div>
		</div>

		{#if loading && !telemetry}
			<div class="flex items-center gap-3 py-8 text-sm text-gray-500 dark:text-gray-400">
				<Spinner className="size-4" />
				<span>{$i18n.t('Loading runtime telemetry...')}</span>
			</div>
		{:else if telemetry}
			<div class="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('State')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{telemetry.enabled ? $i18n.t('Running') : $i18n.t('Stopped')}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Started')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{formatDateTime(telemetry.started_at)}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Events')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{telemetry.total_events}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Model Activity')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{telemetry.model_activity_count}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Fallbacks')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{telemetry.fallback_count}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Kinds')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{Object.entries(telemetry.kind_counts || {})
							.map(([key, value]) => `${key}:${value}`)
							.join(' · ') || '—'}
					</div>
				</div>
			</div>
		{/if}

		{#if lastError}
			<div
				class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
			>
				{lastError}
			</div>
		{/if}
	</div>

	{#if telemetry}
		<div class="grid gap-4 xl:grid-cols-[1.1fr,1.4fr]">
			<div
				class="overflow-hidden rounded-xl border border-gray-100 bg-white dark:border-gray-800 dark:bg-gray-900"
			>
				<div class="border-b border-gray-100 px-4 py-3 text-sm font-medium dark:border-gray-800">
					{$i18n.t('Recent Messages')}
				</div>
				<div class="max-h-[70vh] overflow-auto">
					<table class="w-full text-left text-xs">
						<thead
							class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-900 dark:text-gray-400"
						>
							<tr>
								<th class="px-4 py-2 font-medium">{$i18n.t('Message')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Activity')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Models')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Last Seen')}</th>
							</tr>
						</thead>
						<tbody>
							{#if telemetry.recent_messages.length === 0}
								<tr>
									<td colspan="4" class="px-4 py-6 text-center text-gray-500 dark:text-gray-400">
										{$i18n.t('No runtime telemetry captured yet.')}
									</td>
								</tr>
							{:else}
								{#each telemetry.recent_messages as message}
									<tr class="border-t border-gray-100 align-top dark:border-gray-800">
										<td class="px-4 py-3 text-gray-700 dark:text-gray-200">
											<div class="font-medium">{compactId(message.chat_id, 12)}</div>
											<div class="pt-1 text-[11px] text-gray-500 dark:text-gray-400">
												{compactId(message.message_id, 12)}
											</div>
										</td>
										<td class="px-4 py-3 text-gray-600 dark:text-gray-300">
											<div>{message.event_count} {$i18n.t('events')}</div>
											<div class="pt-1">{message.model_activity_count} {$i18n.t('model')}</div>
											<div class="pt-1">{message.fallback_count} {$i18n.t('fallback')}</div>
											<div class="pt-1 text-[11px] text-gray-500 dark:text-gray-400">
												{formatList(message.task_kinds)}
											</div>
										</td>
										<td class="px-4 py-3 text-[11px] text-gray-600 dark:text-gray-300">
											<div>{formatList(message.models)}</div>
											<div class="pt-1 text-gray-500 dark:text-gray-400">
												{$i18n.t('Active')}: {formatList(message.active_models)}
											</div>
											<div class="pt-1 text-gray-500 dark:text-gray-400">
												{$i18n.t('Ops')}: {formatList(message.operations)}
											</div>
										</td>
										<td class="px-4 py-3 text-gray-500 dark:text-gray-400">
											<div>{formatTs(message.last_seen_at)}</div>
											<div class="pt-1 text-[11px]">{formatTs(message.first_seen_at)}</div>
										</td>
									</tr>
								{/each}
							{/if}
						</tbody>
					</table>
				</div>
			</div>

			<div
				class="overflow-hidden rounded-xl border border-gray-100 bg-white dark:border-gray-800 dark:bg-gray-900"
			>
				<div class="border-b border-gray-100 px-4 py-3 text-sm font-medium dark:border-gray-800">
					{$i18n.t('Recent Events')}
				</div>
				<div class="max-h-[70vh] overflow-auto">
					<table class="w-full text-left text-xs">
						<thead
							class="sticky top-0 bg-gray-50 text-gray-500 dark:bg-gray-900 dark:text-gray-400"
						>
							<tr>
								<th class="px-4 py-2 font-medium">{$i18n.t('Time')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Event')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Model')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Route')}</th>
								<th class="px-4 py-2 font-medium">{$i18n.t('Chat')}</th>
							</tr>
						</thead>
						<tbody>
							{#if telemetry.recent_events.length === 0}
								<tr>
									<td colspan="5" class="px-4 py-6 text-center text-gray-500 dark:text-gray-400">
										{$i18n.t('No runtime telemetry events yet.')}
									</td>
								</tr>
							{:else}
								{#each [...telemetry.recent_events].reverse() as event}
									<tr class="border-t border-gray-100 align-top dark:border-gray-800">
										<td class="px-4 py-3 text-gray-500 dark:text-gray-400">
											<div>{formatTs(event.ts)}</div>
											<div class="pt-1 text-[11px]">#{event.seq}</div>
										</td>
										<td class="px-4 py-3 text-gray-700 dark:text-gray-200">
											<div class="font-medium">{eventSummary(event)}</div>
											<div class="pt-1 text-[11px] text-gray-500 dark:text-gray-400">
												{event.kind}
											</div>
										</td>
										<td class="px-4 py-3 text-[11px] text-gray-600 dark:text-gray-300">
											{eventMeta(event)}
										</td>
										<td class="px-4 py-3 text-[11px] text-gray-600 dark:text-gray-300">
											{#if event.payload?.duration_ms}
												<div>{event.payload.duration_ms} ms</div>
											{/if}
											<div>{event.payload?.reason ?? '—'}</div>
										</td>
										<td class="px-4 py-3 text-[11px] text-gray-500 dark:text-gray-400">
											<div>{compactId(event.chat_id, 12)}</div>
											<div class="pt-1">{compactId(event.message_id, 12)}</div>
										</td>
									</tr>
								{/each}
							{/if}
						</tbody>
					</table>
				</div>
			</div>
		</div>
	{/if}
</div>
