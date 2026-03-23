<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	import WorkflowLessons from '$lib/components/admin/WorkflowLessons.svelte';

	onMount(() => {
		goto('/admin/workflow-lessons/repeated');
	});
</script>

<WorkflowLessons />
