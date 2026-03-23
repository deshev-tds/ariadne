import { WEBUI_API_BASE_URL } from '$lib/constants';

export type WorkflowLessonRow = {
	lesson_id: string;
	status: string;
	working_mode: string;
	workflow_family: string;
	title: string;
	applies_when: string[];
	prefer: string[];
	avoid: string[];
	signal: string[];
	source_turn_ids: string[];
	updated_at: string;
	registry_version?: string;
	pattern_key?: string;
	condition_codes?: string[];
	prefer_codes?: string[];
	avoid_codes?: string[];
	signal_codes?: string[];
	do_not_apply_when?: string[];
	confidence_note?: string;
	evidence_refs?: string[];
	origin?: string;
	can_unpromote?: boolean;
	unpromote_reason?: string | null;
};

export type WorkflowLessonsReviewSummary = {
	runtime_root: string;
	observed_rows: number;
	registry_backed_observed_rows: number;
	unique_signatures: number;
	repeated_candidates: number;
	digest_present: boolean;
};

export type WorkflowRepeatedCandidate = {
	version: number;
	kind: string;
	candidate_id: string;
	signature: string;
	registry_version: string;
	working_mode: string;
	workflow_family: string;
	pattern_key: string;
	condition_codes: string[];
	prefer_codes: string[];
	avoid_codes: string[];
	signal_codes: string[];
	title: string;
	applies_when: string[];
	prefer: string[];
	avoid: string[];
	signal: string[];
	occurrence_count: number;
	distinct_chat_count: number;
	source_turn_ids: string[];
	source_chat_ids: string[];
	source_observed_lesson_ids: string[];
	first_seen_at: string;
	last_seen_at: string;
	origin: string;
	suggested_lesson_id: string;
	existing_curated_lesson_id: string | null;
	can_promote: boolean;
};

export type WorkflowLessonsState = {
	runtime_root: string;
	curated_root: string;
	runtime: {
		observed_rows: WorkflowLessonRow[];
		repeated_candidates: WorkflowRepeatedCandidate[];
		review_summary: WorkflowLessonsReviewSummary | null;
		review_digest_markdown: string | null;
	};
	curated: {
		promoted_rows: WorkflowLessonRow[];
	};
};

export type WorkflowLessonsReviewResponse = {
	review_summary: WorkflowLessonsReviewSummary;
	state: WorkflowLessonsState;
};

export type WorkflowLessonsPromoteResponse = {
	export_summary: {
		runtime_root: string;
		curated_root: string;
		candidate_id: string;
		target_lesson_id: string;
		replaced: boolean;
		dry_run: boolean;
		serving_root: string;
	};
	state: WorkflowLessonsState;
};

export type WorkflowLessonsUnpromoteResponse = {
	unpromote_summary: {
		curated_root: string;
		lesson_id: string;
		removed: boolean;
		dry_run: boolean;
		serving_root: string;
	};
	state: WorkflowLessonsState;
};

const fetchWorkflowLessons = async <T>(
	path: string,
	token: string,
	options: RequestInit = {}
): Promise<T> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/workflow-lessons${path}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...options
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			error = err.detail ?? err.message ?? 'Request failed';
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res as T;
};

export const getWorkflowLessonsState = async (
	token: string = ''
): Promise<WorkflowLessonsState> => {
	return fetchWorkflowLessons<WorkflowLessonsState>('/state', token);
};

export const runWorkflowLessonsReview = async (
	token: string = ''
): Promise<WorkflowLessonsReviewResponse> => {
	return fetchWorkflowLessons<WorkflowLessonsReviewResponse>('/review', token, {
		method: 'POST'
	});
};

export const promoteWorkflowLessonCandidate = async (
	token: string,
	candidateId: string,
	targetLessonId: string
): Promise<WorkflowLessonsPromoteResponse> => {
	return fetchWorkflowLessons<WorkflowLessonsPromoteResponse>('/promote', token, {
		method: 'POST',
		body: JSON.stringify({
			candidate_id: candidateId,
			target_lesson_id: targetLessonId
		})
	});
};

export const unpromoteWorkflowLesson = async (
	token: string,
	lessonId: string
): Promise<WorkflowLessonsUnpromoteResponse> => {
	return fetchWorkflowLessons<WorkflowLessonsUnpromoteResponse>('/unpromote', token, {
		method: 'POST',
		body: JSON.stringify({
			lesson_id: lessonId
		})
	});
};
