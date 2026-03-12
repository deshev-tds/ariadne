<script lang="ts">
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';

	export let status = { urls: [], query: '' };
	let state = false;

	const toFixed2 = (value: unknown) => {
		const n = Number(value);
		return Number.isFinite(n) ? n.toFixed(2) : null;
	};
</script>

<Collapsible grow={true} className="w-full" buttonClassName="w-full" bind:open={state}>
	<div class="flex items-center gap-2 text-gray-500 transition">
		<slot />
		{#if state}
			<ChevronUp strokeWidth="2.5" className="size-3.5 " />
		{:else}
			<ChevronDown strokeWidth="2.5" className="size-3.5 " />
		{/if}
	</div>

		<div
			class="text-sm border border-gray-50 dark:border-gray-850/30 rounded-xl my-1.5 p-2 w-full"
			slot="content"
		>
			{#if status?.plan || status?.planner}
				<div class="mb-2 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-850/50 border border-gray-100 dark:border-gray-800">
					<div class="flex flex-wrap gap-1.5 text-[11px] text-gray-600 dark:text-gray-300">
						{#if status?.planner?.mode || status?.plan?.mode}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Planner: {status?.planner?.mode ?? status?.plan?.mode}
							</div>
						{/if}
						{#if status?.plan?.intent}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Intent: {status.plan.intent}
							</div>
						{/if}
						{#if status?.plan?.topic}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Topic: {status.plan.topic}
							</div>
						{/if}
						{#if status?.planner?.stop_reason}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Stop: {status.planner.stop_reason}
							</div>
						{/if}
						{#if status?.planner?.executed_queries?.length}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Calls: {status.planner.executed_queries.length}{#if status?.planner?.max_total_queries
								}/{status.planner.max_total_queries}{/if}
							</div>
						{/if}
						{#if status?.planner?.show_debug_metrics && toFixed2(status?.planner?.final_score)}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Score: {toFixed2(status.planner.final_score)}
							</div>
						{/if}
						{#if status?.planner?.candidate_count !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Candidate pool: {status.planner.candidate_count}
							</div>
						{/if}
						{#if status?.planner?.evidence_count !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Evidence used: {status.planner.evidence_count}
							</div>
						{/if}
						{#if status?.planner?.citation_count !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Citations shown: {status.planner.citation_count}
							</div>
						{/if}
						{#if status?.planner?.final_trusted_domains !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Trusted domains: {status.planner.final_trusted_domains}
							</div>
						{/if}
						{#if status?.planner?.rewriter_model_used}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Rewriter: {status.planner.rewriter_model_used}
							</div>
						{/if}
						{#if status?.planner?.rewriter_retry_count !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Retry: {status.planner.rewriter_retry_count}
							</div>
						{/if}
						{#if status?.planner?.rewriter_fallback_used}
							<div class="px-2 py-0.5 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800 text-red-600 dark:text-red-300">
								Rewriter fallback used
							</div>
						{/if}
						{#if status?.planner?.fallback_reason}
							<div class="px-2 py-0.5 rounded-md bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800 text-amber-700 dark:text-amber-300 line-clamp-1">
								Fallback: {status.planner.fallback_reason}
							</div>
						{/if}
						{#if status?.loaded_count !== undefined}
							<div class="px-2 py-0.5 rounded-md bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
								Loaded docs: {status.loaded_count}
							</div>
						{/if}
					</div>

					{#if status?.plan?.selected_domains?.length}
						<div class="mt-2 text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2">
							Domains: {status.plan.selected_domains.join(', ')}
						</div>
					{/if}

					{#if status?.planner?.executed_queries?.length}
						<div class="mt-2 flex gap-1 flex-wrap">
							{#each status.planner.executed_queries as executedQuery (executedQuery)}
								<div class="bg-white dark:bg-gray-900 flex rounded-lg py-1 px-2 items-center gap-1 text-[11px] border border-gray-100 dark:border-gray-800">
									<Search className="size-3" />
									<span class="line-clamp-1">{executedQuery}</span>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			{#if status?.query}
				<a
					href="https://www.google.com/search?q={status.query}"
				target="_blank"
				class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 font-normal! no-underline!"
			>
				<div class="flex gap-2 items-center">
					<Search />

					<div class=" line-clamp-1">
						{status.query}
					</div>
				</div>

				<div
					class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
				>
					<!--  -->
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 16 16"
						fill="currentColor"
						class="size-4"
					>
						<path
							fill-rule="evenodd"
							d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
			</a>
		{/if}

		{#if status?.items}
			{#each status.items as item, itemIdx}
				<a
					href={item.link}
					target="_blank"
					class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850 rounded-lg font-normal! no-underline! mb-1"
				>
					<div class=" flex justify-center items-center gap-3">
						<div class="w-fit">
							<img
								src="https://www.google.com/s2/favicons?sz=32&domain={item.link}"
								alt="{item?.title ?? item.link} favicon"
								class="size-3.5"
							/>
						</div>

						<div class="w-full text-sm line-clamp-1">
							{item?.title ?? item.link}
						</div>
					</div>

					<div
						class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
					>
						<!--  -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="size-4"
						>
							<path
								fill-rule="evenodd"
								d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
				</a>
			{/each}
		{:else if status?.urls}
			{#each status.urls as url, urlIdx}
				<a
					href={url}
					target="_blank"
					class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850 rounded-lg no-underline mb-1"
				>
					<div class=" flex justify-center items-center gap-3">
						<div class="w-fit">
							<img
								src="https://www.google.com/s2/favicons?sz=32&domain={url}"
								alt="{url} favicon"
								class="size-3.5"
							/>
						</div>

						<div class="w-full text-sm line-clamp-1">
							{url}
						</div>
					</div>

					<div
						class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
					>
						<!--  -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="size-4"
						>
							<path
								fill-rule="evenodd"
								d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
				</a>
			{/each}
		{/if}
	</div>
</Collapsible>
