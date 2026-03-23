<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { WEBUI_NAME, user } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getWorkflowLessonsState,
		promoteWorkflowLessonCandidate,
		runWorkflowLessonsReview,
		type WorkflowLessonRow,
		type WorkflowLessonsState,
		type WorkflowRepeatedCandidate
	} from '$lib/apis/workflow-lessons';
	import {
		getChatHrefFromSourceTurnId,
		normalizeWorkflowLessonsTab,
		type WorkflowLessonsTab
	} from './workflowLessons';

	let loaded = false;
	let loading = true;
	let reviewLoading = false;
	let promoteLoadingId: string | null = null;
	let lastError = '';
	let state: WorkflowLessonsState | null = null;

	let searchQuery = '';
	let familyFilter: 'all' | 'research' | 'offsec' = 'all';

	let repeatedSelectionId: string | null = null;
	let observedSelectionId: string | null = null;
	let promotedSelectionId: string | null = null;
	let promoteDrafts: Record<string, string> = {};
	let repeatedCandidates: WorkflowRepeatedCandidate[] = [];
	let observedRows: WorkflowLessonRow[] = [];
	let promotedRows: WorkflowLessonRow[] = [];
	let selectedRepeatedCandidate: WorkflowRepeatedCandidate | null = null;
	let selectedObservedRow: WorkflowLessonRow | null = null;
	let selectedPromotedRow: WorkflowLessonRow | null = null;

	$: currentTab = normalizeWorkflowLessonsTab($page.params.tab);
	$: observedCount = state?.runtime.observed_rows.length ?? 0;
	$: repeatedCount = state?.runtime.repeated_candidates.length ?? 0;
	$: promotedCount = state?.curated.promoted_rows.length ?? 0;
	$: statusCopy = !state
		? ''
		: observedCount === 0 && repeatedCount === 0 && !state.runtime.review_summary
			? 'No runtime workflow lessons catalog found yet.'
			: repeatedCount === 0
				? 'No repeated candidates yet. Run review after materializing more observed lessons.'
				: 'Runtime review artifacts are available.';

	const normalizeSearch = (value: string) => value.trim().toLowerCase();

	const familyLabel = (family: string) => (family === 'offsec' ? 'Offsec' : 'Research');

	const formatDateTime = (value?: string | null) => {
		if (!value) return '—';
		const parsed = new Date(value);
		if (Number.isNaN(parsed.getTime())) {
			return value;
		}
		return parsed.toLocaleString();
	};

	const familyMatches = (family: string) => familyFilter === 'all' || family === familyFilter;

	const queryMatches = (values: Array<string | null | undefined>) => {
		const query = normalizeSearch(searchQuery);
		if (!query) return true;
		return values.some((value) => String(value ?? '').toLowerCase().includes(query));
	};

	const applyState = (nextState: WorkflowLessonsState) => {
		state = nextState;
		const nextDrafts = { ...promoteDrafts };
		for (const candidate of nextState.runtime.repeated_candidates) {
			if (!nextDrafts[candidate.candidate_id]?.trim()) {
				nextDrafts[candidate.candidate_id] = candidate.suggested_lesson_id;
			}
		}
		promoteDrafts = nextDrafts;
	};

	const loadState = async () => {
		loading = true;
		lastError = '';
		try {
			applyState(await getWorkflowLessonsState(localStorage.token));
		} catch (error) {
			console.error('Workflow lessons state load failed:', error);
			lastError = `${error}`;
		} finally {
			loading = false;
		}
	};

	const openTab = async (tab: WorkflowLessonsTab) => {
		await goto(`/admin/workflow-lessons/${tab}`);
	};

	const runReview = async () => {
		reviewLoading = true;
		lastError = '';
		try {
			const response = await runWorkflowLessonsReview(localStorage.token);
			applyState(response.state);
			toast.success(
				`Review refreshed: ${response.review_summary.repeated_candidates} repeated candidate(s).`
			);
		} catch (error) {
			console.error('Workflow lessons review failed:', error);
			lastError = `${error}`;
			toast.error(`${error}`);
		} finally {
			reviewLoading = false;
		}
	};

	const promoteCandidate = async (candidate: WorkflowRepeatedCandidate) => {
		const targetLessonId = String(promoteDrafts[candidate.candidate_id] ?? '').trim();
		if (!targetLessonId) {
			toast.error('Target lesson id is required.');
			return;
		}

		promoteLoadingId = candidate.candidate_id;
		lastError = '';
		try {
			const response = await promoteWorkflowLessonCandidate(
				localStorage.token,
				candidate.candidate_id,
				targetLessonId
			);
			applyState(response.state);
			toast.success(`Promoted as ${response.export_summary.target_lesson_id}.`);
		} catch (error) {
			console.error('Workflow lesson promote failed:', error);
			lastError = `${error}`;
			toast.error(`${error}`);
		} finally {
			promoteLoadingId = null;
		}
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
			return;
		}
		loaded = true;
		await loadState();
	});

	$: repeatedCandidates =
		state?.runtime.repeated_candidates.filter(
			(candidate) =>
				familyMatches(candidate.workflow_family) &&
				queryMatches([candidate.title, candidate.pattern_key, candidate.candidate_id])
		) ?? [];

	$: observedRows =
		state?.runtime.observed_rows.filter(
			(row) =>
				familyMatches(row.workflow_family) &&
				queryMatches([row.title, row.pattern_key, row.workflow_family, row.lesson_id])
		) ?? [];

	$: promotedRows =
		state?.curated.promoted_rows.filter(
			(row) =>
				familyMatches(row.workflow_family) &&
				queryMatches([row.title, row.pattern_key, row.workflow_family, row.lesson_id])
		) ?? [];

	$: if (repeatedCandidates.length === 0) {
		repeatedSelectionId = null;
	} else if (!repeatedCandidates.some((item) => item.candidate_id === repeatedSelectionId)) {
		repeatedSelectionId = repeatedCandidates[0].candidate_id;
	}

	$: if (observedRows.length === 0) {
		observedSelectionId = null;
	} else if (!observedRows.some((item) => item.lesson_id === observedSelectionId)) {
		observedSelectionId = observedRows[0].lesson_id;
	}

	$: if (promotedRows.length === 0) {
		promotedSelectionId = null;
	} else if (!promotedRows.some((item) => item.lesson_id === promotedSelectionId)) {
		promotedSelectionId = promotedRows[0].lesson_id;
	}

	$: selectedRepeatedCandidate =
		repeatedCandidates.find((item) => item.candidate_id === repeatedSelectionId) ?? null;
	$: selectedObservedRow = observedRows.find((item) => item.lesson_id === observedSelectionId) ?? null;
	$: selectedPromotedRow = promotedRows.find((item) => item.lesson_id === promotedSelectionId) ?? null;
</script>

<svelte:head>
	<title>Workflow Lessons • {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="w-full h-full pb-2 px-[16px]">
		<div class="flex flex-col gap-4">
			<div
				class="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
			>
				<div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
					<div class="space-y-1">
						<div class="text-lg font-medium text-gray-900 dark:text-white">
							Workflow Lessons
						</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">
							Thin operator UI for runtime observed rows, repeated candidates and curated promoted
							lessons.
						</div>
						{#if statusCopy}
							<div class="text-xs text-gray-500 dark:text-gray-400">{statusCopy}</div>
						{/if}
					</div>

					<div class="flex flex-wrap items-center gap-2">
						<div
							class="rounded-md bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
						>
							Observed: {observedCount}
						</div>
						<div
							class="rounded-md bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
						>
							Repeated: {repeatedCount}
						</div>
						<div
							class="rounded-md bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
						>
							Promoted: {promotedCount}
						</div>
						<button
							class="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
							disabled={loading || reviewLoading}
							on:click={runReview}
						>
							{#if reviewLoading}
								<span class="inline-flex items-center gap-2">
									<Spinner className="size-3" />
									<span>Running Review</span>
								</span>
							{:else}
								Run Review
							{/if}
						</button>
					</div>
				</div>

				{#if lastError}
					<div
						class="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
					>
						{lastError}
					</div>
				{/if}
			</div>

			{#if loading && !state}
				<div
					class="flex items-center gap-3 rounded-xl border border-gray-100 bg-white px-4 py-8 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400"
				>
					<Spinner className="size-4" />
					<span>Loading workflow lessons...</span>
				</div>
			{:else}
				<div class="flex flex-col lg:flex-row w-full h-full pb-2 lg:space-x-4">
					<div
						class="tabs mx-[16px] lg:mx-0 lg:px-[16px] flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-56 dark:text-gray-200 text-sm font-medium text-left scrollbar-none"
					>
						<button
							class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-left transition select-none {currentTab ===
							'repeated'
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
							on:click={() => openTab('repeated')}
						>
							Repeated
						</button>
						<button
							class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-left transition select-none {currentTab ===
							'observed'
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
							on:click={() => openTab('observed')}
						>
							Observed
						</button>
						<button
							class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-left transition select-none {currentTab ===
							'promoted'
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
							on:click={() => openTab('promoted')}
						>
							Promoted
						</button>
					</div>

					<div class="flex-1 mt-1 lg:mt-0 px-[16px] lg:pr-[16px] lg:pl-0 overflow-y-auto">
						<div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px] pb-4">
							<input
								class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:focus:border-gray-500"
								bind:value={searchQuery}
								placeholder="Search title, ids or pattern key"
							/>
							<select
								class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:focus:border-gray-500"
								bind:value={familyFilter}
							>
								<option value="all">All families</option>
								<option value="research">Research</option>
								<option value="offsec">Offsec</option>
							</select>
						</div>

						{#if currentTab === 'repeated'}
							<div class="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(360px,1.1fr)]">
								<div
									class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
								>
									<div class="pb-3 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
										Repeated Candidates
									</div>
									{#if repeatedCandidates.length === 0}
										<div class="py-8 text-sm text-gray-500 dark:text-gray-400">
											No repeated candidates match the current filters.
										</div>
									{:else}
										<div class="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
											{#each repeatedCandidates as candidate}
												<button
													class="rounded-lg border px-3 py-3 text-left transition {candidate.candidate_id ===
													repeatedSelectionId
														? 'border-gray-900 bg-gray-50 dark:border-white dark:bg-gray-800'
														: 'border-gray-100 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700'}"
													on:click={() => {
														repeatedSelectionId = candidate.candidate_id;
													}}
												>
													<div class="flex items-start justify-between gap-3">
														<div class="space-y-1">
															<div class="text-sm font-medium text-gray-900 dark:text-white">
																{candidate.title}
															</div>
															<div class="text-xs text-gray-500 dark:text-gray-400">
																{candidate.pattern_key}
															</div>
														</div>
														<div
															class="rounded-md bg-gray-100 px-2 py-1 text-[11px] font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
														>
															{familyLabel(candidate.workflow_family)}
														</div>
													</div>
													<div class="pt-2 text-xs text-gray-500 dark:text-gray-400">
														{candidate.distinct_chat_count} chats · {candidate.occurrence_count} occurrences
													</div>
													{#if candidate.existing_curated_lesson_id}
														<div class="pt-2">
															<span
																class="rounded-full border border-emerald-200 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:border-emerald-900/60 dark:text-emerald-300"
															>
																Already promoted
															</span>
														</div>
													{/if}
												</button>
											{/each}
										</div>
									{/if}
								</div>

								<div
									class="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
								>
									{#if selectedRepeatedCandidate}
										<div class="space-y-4">
											<div class="space-y-1">
												<div class="text-lg font-medium text-gray-900 dark:text-white">
													{selectedRepeatedCandidate.title}
												</div>
												<div class="text-xs text-gray-500 dark:text-gray-400">
													{selectedRepeatedCandidate.candidate_id}
												</div>
											</div>

											<div class="grid gap-2 sm:grid-cols-2">
												<div class="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800/70">
													<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
														Pattern
													</div>
													<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
														{selectedRepeatedCandidate.pattern_key}
													</div>
												</div>
												<div class="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800/70">
													<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
														Registry
													</div>
													<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
														{selectedRepeatedCandidate.registry_version}
													</div>
												</div>
												<div class="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800/70">
													<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
														Distinct Chats
													</div>
													<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
														{selectedRepeatedCandidate.distinct_chat_count}
													</div>
												</div>
												<div class="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800/70">
													<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
														Occurrences
													</div>
													<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
														{selectedRepeatedCandidate.occurrence_count}
													</div>
												</div>
											</div>

											<div class="space-y-3 text-sm text-gray-700 dark:text-gray-300">
												<div>
													<div class="font-medium text-gray-900 dark:text-white">Applies When</div>
													<ul class="pt-1 space-y-1 list-disc list-inside">
														{#each selectedRepeatedCandidate.applies_when as item}
															<li>{item}</li>
														{/each}
													</ul>
												</div>
												<div>
													<div class="font-medium text-gray-900 dark:text-white">Prefer</div>
													<ul class="pt-1 space-y-1 list-disc list-inside">
														{#each selectedRepeatedCandidate.prefer as item}
															<li>{item}</li>
														{/each}
													</ul>
												</div>
												<div>
													<div class="font-medium text-gray-900 dark:text-white">Avoid</div>
													<ul class="pt-1 space-y-1 list-disc list-inside">
														{#each selectedRepeatedCandidate.avoid as item}
															<li>{item}</li>
														{/each}
													</ul>
												</div>
												<div>
													<div class="font-medium text-gray-900 dark:text-white">Signal</div>
													<ul class="pt-1 space-y-1 list-disc list-inside">
														{#each selectedRepeatedCandidate.signal as item}
															<li>{item}</li>
														{/each}
													</ul>
												</div>
											</div>

											<div class="space-y-2">
												<div class="font-medium text-sm text-gray-900 dark:text-white">
													Canonical Codes
												</div>
												<div class="flex flex-wrap gap-2">
													{#each selectedRepeatedCandidate.condition_codes as code}
														<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
															{code}
														</span>
													{/each}
													{#each selectedRepeatedCandidate.prefer_codes as code}
														<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
															{code}
														</span>
													{/each}
													{#each selectedRepeatedCandidate.avoid_codes as code}
														<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
															{code}
														</span>
													{/each}
													{#each selectedRepeatedCandidate.signal_codes as code}
														<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
															{code}
														</span>
													{/each}
												</div>
											</div>

											<div class="space-y-2">
												<div class="font-medium text-sm text-gray-900 dark:text-white">
													Source Turns
												</div>
												<div class="flex flex-col gap-1 text-sm">
													{#each selectedRepeatedCandidate.source_turn_ids as turnId}
														{#if getChatHrefFromSourceTurnId(turnId)}
															<a
																class="text-blue-600 hover:underline dark:text-blue-400"
																href={getChatHrefFromSourceTurnId(turnId) ?? '#'}
															>
																{turnId}
															</a>
														{:else}
															<span>{turnId}</span>
														{/if}
													{/each}
												</div>
											</div>

											<div class="space-y-2 rounded-lg border border-gray-100 p-3 dark:border-gray-800">
												<div class="font-medium text-sm text-gray-900 dark:text-white">
													Promote
												</div>
												<input
													class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-gray-400 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:focus:border-gray-500"
													bind:value={promoteDrafts[selectedRepeatedCandidate.candidate_id]}
													disabled={!selectedRepeatedCandidate.can_promote || promoteLoadingId === selectedRepeatedCandidate.candidate_id}
												/>
												<div class="flex items-center justify-between gap-3">
													{#if selectedRepeatedCandidate.existing_curated_lesson_id}
														<div class="text-xs text-emerald-700 dark:text-emerald-300">
															Already promoted as {selectedRepeatedCandidate.existing_curated_lesson_id}
														</div>
													{:else}
														<div class="text-xs text-gray-500 dark:text-gray-400">
															Prefilled with the canonical pattern key. Edit before export if needed.
														</div>
													{/if}

													<button
														class="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
														disabled={!selectedRepeatedCandidate.can_promote || promoteLoadingId === selectedRepeatedCandidate.candidate_id}
														on:click={() => promoteCandidate(selectedRepeatedCandidate)}
													>
														{#if promoteLoadingId === selectedRepeatedCandidate.candidate_id}
															<span class="inline-flex items-center gap-2">
																<Spinner className="size-3" />
																<span>Promoting</span>
															</span>
														{:else}
															Promote
														{/if}
													</button>
												</div>
											</div>
										</div>
									{:else}
										<div class="py-12 text-sm text-gray-500 dark:text-gray-400">
											Select a repeated candidate to inspect its details.
										</div>
									{/if}
								</div>
							</div>

							<div
								class="mt-4 rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
							>
								<div class="pb-3 text-sm font-medium text-gray-900 dark:text-white">Review Digest</div>
								{#if state?.runtime.review_digest_markdown}
									<div
										class="max-h-[28rem] overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 font-mono text-xs text-gray-700 dark:bg-gray-800/70 dark:text-gray-200"
									>
										{state.runtime.review_digest_markdown}
									</div>
								{:else}
									<div class="text-sm text-gray-500 dark:text-gray-400">
										No review digest is available yet.
									</div>
								{/if}
							</div>
						{:else if currentTab === 'observed'}
							<div class="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(360px,1.1fr)]">
								<div
									class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
								>
									<div class="pb-3 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
										Observed Rows
									</div>
									{#if observedRows.length === 0}
										<div class="py-8 text-sm text-gray-500 dark:text-gray-400">
											No observed rows match the current filters.
										</div>
									{:else}
										<div class="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
											{#each observedRows as row}
												<button
													class="rounded-lg border px-3 py-3 text-left transition {row.lesson_id ===
													observedSelectionId
														? 'border-gray-900 bg-gray-50 dark:border-white dark:bg-gray-800'
														: 'border-gray-100 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700'}"
													on:click={() => {
														observedSelectionId = row.lesson_id;
													}}
												>
													<div class="space-y-1">
														<div class="text-sm font-medium text-gray-900 dark:text-white">
															{row.title}
														</div>
														<div class="text-xs text-gray-500 dark:text-gray-400">
															{row.lesson_id}
														</div>
													</div>
													<div class="pt-2 text-xs text-gray-500 dark:text-gray-400">
														{row.pattern_key ?? 'non-registry'} · {familyLabel(row.workflow_family)}
													</div>
												</button>
											{/each}
										</div>
									{/if}
								</div>

								<div
									class="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
								>
									{#if selectedObservedRow}
										<div class="space-y-4">
											<div class="space-y-1">
												<div class="text-lg font-medium text-gray-900 dark:text-white">
													{selectedObservedRow.title}
												</div>
												<div class="text-xs text-gray-500 dark:text-gray-400">
													{selectedObservedRow.lesson_id}
												</div>
											</div>
											<div class="text-sm text-gray-700 dark:text-gray-300">
												<div class="font-medium text-gray-900 dark:text-white">Applies When</div>
												<ul class="pt-1 space-y-1 list-disc list-inside">
													{#each selectedObservedRow.applies_when as item}
														<li>{item}</li>
													{/each}
												</ul>
											</div>
											<div class="flex flex-wrap gap-2">
												{#each selectedObservedRow.condition_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedObservedRow.prefer_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedObservedRow.avoid_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedObservedRow.signal_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
											</div>
											<div class="space-y-2 text-sm text-gray-700 dark:text-gray-300">
												<div>
													<span class="font-medium text-gray-900 dark:text-white">Pattern:</span>
													{selectedObservedRow.pattern_key ?? '—'}
												</div>
												<div>
													<span class="font-medium text-gray-900 dark:text-white">Updated:</span>
													{formatDateTime(selectedObservedRow.updated_at)}
												</div>
												<div>
													<span class="font-medium text-gray-900 dark:text-white">Origin:</span>
													{selectedObservedRow.origin ?? '—'}
												</div>
											</div>
											<div class="space-y-2">
												<div class="font-medium text-sm text-gray-900 dark:text-white">
													Source Turns
												</div>
												<div class="flex flex-col gap-1 text-sm">
													{#each selectedObservedRow.source_turn_ids as turnId}
														{#if getChatHrefFromSourceTurnId(turnId)}
															<a
																class="text-blue-600 hover:underline dark:text-blue-400"
																href={getChatHrefFromSourceTurnId(turnId) ?? '#'}
															>
																{turnId}
															</a>
														{:else}
															<span>{turnId}</span>
														{/if}
													{/each}
												</div>
											</div>
										</div>
									{:else}
										<div class="py-12 text-sm text-gray-500 dark:text-gray-400">
											Select an observed row to inspect its details.
										</div>
									{/if}
								</div>
							</div>
						{:else}
							<div class="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(360px,1.1fr)]">
								<div
									class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
								>
									<div class="pb-3 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
										Promoted Lessons
									</div>
									{#if promotedRows.length === 0}
										<div class="py-8 text-sm text-gray-500 dark:text-gray-400">
											No promoted lessons match the current filters.
										</div>
									{:else}
										<div class="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
											{#each promotedRows as row}
												<button
													class="rounded-lg border px-3 py-3 text-left transition {row.lesson_id ===
													promotedSelectionId
														? 'border-gray-900 bg-gray-50 dark:border-white dark:bg-gray-800'
														: 'border-gray-100 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700'}"
													on:click={() => {
														promotedSelectionId = row.lesson_id;
													}}
												>
													<div class="space-y-1">
														<div class="text-sm font-medium text-gray-900 dark:text-white">
															{row.title}
														</div>
														<div class="text-xs text-gray-500 dark:text-gray-400">
															{row.lesson_id}
														</div>
													</div>
													<div class="pt-2 text-xs text-gray-500 dark:text-gray-400">
														{row.pattern_key ?? 'non-registry'} · {familyLabel(row.workflow_family)}
													</div>
												</button>
											{/each}
										</div>
									{/if}
								</div>

								<div
									class="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
								>
									{#if selectedPromotedRow}
										<div class="space-y-4">
											<div class="space-y-1">
												<div class="text-lg font-medium text-gray-900 dark:text-white">
													{selectedPromotedRow.title}
												</div>
												<div class="text-xs text-gray-500 dark:text-gray-400">
													{selectedPromotedRow.lesson_id}
												</div>
											</div>
											<div class="text-sm text-gray-700 dark:text-gray-300">
												<div class="font-medium text-gray-900 dark:text-white">Applies When</div>
												<ul class="pt-1 space-y-1 list-disc list-inside">
													{#each selectedPromotedRow.applies_when as item}
														<li>{item}</li>
													{/each}
												</ul>
											</div>
											<div class="flex flex-wrap gap-2">
												{#each selectedPromotedRow.condition_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedPromotedRow.prefer_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedPromotedRow.avoid_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
												{#each selectedPromotedRow.signal_codes ?? [] as code}
													<span class="rounded-md bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-200">
														{code}
													</span>
												{/each}
											</div>
											<div class="space-y-2 text-sm text-gray-700 dark:text-gray-300">
												<div>
													<span class="font-medium text-gray-900 dark:text-white">Pattern:</span>
													{selectedPromotedRow.pattern_key ?? '—'}
												</div>
												<div>
													<span class="font-medium text-gray-900 dark:text-white">Updated:</span>
													{formatDateTime(selectedPromotedRow.updated_at)}
												</div>
											</div>
											<div class="space-y-2">
												<div class="font-medium text-sm text-gray-900 dark:text-white">
													Source Turns
												</div>
												<div class="flex flex-col gap-1 text-sm">
													{#each selectedPromotedRow.source_turn_ids as turnId}
														{#if getChatHrefFromSourceTurnId(turnId)}
															<a
																class="text-blue-600 hover:underline dark:text-blue-400"
																href={getChatHrefFromSourceTurnId(turnId) ?? '#'}
															>
																{turnId}
															</a>
														{:else}
															<span>{turnId}</span>
														{/if}
													{/each}
												</div>
											</div>
										</div>
									{:else}
										<div class="py-12 text-sm text-gray-500 dark:text-gray-400">
											Select a promoted lesson to inspect its details.
										</div>
									{/if}
								</div>
							</div>
						{/if}
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}
