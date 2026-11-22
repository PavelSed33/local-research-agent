# Аудит полного Python CI: прогон 43577741179 (14.11.2026)

Источник: GitHub Actions, Python 3.12, job 132395592723. Это реестр **первоначально выявленных** сбоев, а не подтверждение их исправления.

**Всего:** 794 теста; 100 ERROR; 28 FAIL; 4 skipped. Python 3.11: 100 ERROR и 28 FAIL; 3.10: 100 ERROR и 29 FAIL. Docker smoke прошёл.

## Общие причины и тактика

- **Реальные дефекты логики / безопасности:** неполная санитизация LLM-ответов (`verified`, `lawyer_reviewed`, `legal_basis`), ошибки даты и валюты, утечка идентификаторов в сообщениях, несовместимость интерфейса модельных промптов, ошибочная демо-цитата, экспорт доказательств без необходимого контракта. Исправлять и проверять регрессионными тестами.
- **Отсутствующие HTTP-подсистемы:** пользовательское рабочее пространство и ACL (`workspace_db`), управление заданиями/отменой/тайм-аутом (`request_timeout_seconds`), дополнительные endpoints и OpenAPI. Восстанавливать полноценный код и изоляцию, не заглушать статусы.
- **Юридические данные / первоисточники:** 19 сбоев `test_statute_corpus` и 11 `test_primary_archive` связаны с отсутствующими/изолированными исходными записями и файлами. Не возвращать непроверенные данные в поиск ради CI; сначала восстановить права на распространение, оригиналы, идентичность, редакции, SHA-256, provenance и законодательную приёмку.
- **Другие CLI/поставки:** backup, web-UI, readiness, качество отчётов, package, статические ресурсы. Требуют отдельной совместимости и E2E.

## Полный перечень по модулю

| Модуль | ERROR + FAIL (с учётом параметризованных случаев) |
|---|---:|
| `test_statute_corpus` | 19 |
| `test_workspace_http` | 13 |
| `test_request_jobs` | 12 |
| `test_primary_archive` | 11 |
| `test_audit_regressions` | 7 |
| `test_audit_fixes` | 6 |
| `test_live_acceptance` | 6 |
| `test_response_quality` | 6 |
| `test_readiness` | 5 |
| `test_case_report` | 3 |
| `test_data_audit` | 3 |
| `test_legal_skills` | 3 |
| `test_local_extract` | 3 |
| `test_workspace_backup` | 3 |
| `test_document_privacy` | 2 |
| `test_skill_service_health` | 2 |
| `test_workbench` | 2 |
| `test_workspace_report_ui` | 2 |
| `test_api` | 2 |
| `test_audit` | 2 |
| `test_backup_acceptance` | 1 |
| `test_contract_scope` | 1 |
| `test_decision_revisions` | 1 |
| `test_doctext` | 1 |
| `test_document_hardening` | 1 |
| `test_document_tools` | 1 |
| `test_evidence_status` | 1 |
| `test_legal_p0` | 1 |
| `test_legal_research` | 1 |
| `test_outbound_security` | 1 |
| `test_period_selection` | 1 |
| `test_review` | 1 |
| `test_scoring` | 1 |
| `test_session_delete` | 1 |
| `test_consumer_evidence` | 1 |
| `test_packaging` | 1 |

## Детали всех сбоев

### test_statute_corpus — 19
- ERROR: `test_corrupt_file_is_excluded_without_serving_its_text`
- ERROR: `test_manifest_flags_cannot_promote_a_copy_to_an_accepted_law`
- ERROR: `test_path_escape_and_symlink_are_rejected` (name='../legal_coverage.json')
- ERROR: `test_path_escape_and_symlink_are_rejected` (name='/etc/passwd')
- ERROR: `test_path_escape_and_symlink_are_rejected`
- ERROR: `test_refresh_rejects_unpinned_urls_and_does_not_redate_stale_bytes`
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'text': 'Подменённый текст с достаточным количеством символов.'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'url': 'https://www.consultant.ru/document/cons_doc_LAW_9027/other/'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'url': 'https://www.consultant.ru.evil.example/document/'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'url': 'https://www.consultant.ru:1234/document/'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'kind': 'official_publication'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'act_id': 'gk2'})
- ERROR: `test_rehashed_file_still_checks_text_hash_and_exact_act_url` (changes={'provision': '100'})
- ERROR: `test_text_is_requested_explicitly_and_keeps_article_identity`
- FAIL: `test_failed_partial_import_preserves_old_texts_and_original_capture_dates`
- FAIL: `test_later_copy_is_not_injected_into_historical_model_context`
- FAIL: `test_local_search_keeps_exact_article_and_act_and_historical_cutoff`
- FAIL: `test_received_texts_cover_the_declared_scope_without_claiming_legal_acceptance`
- FAIL: `test_special_and_consumer_articles_are_not_crowded_out_by_general_law`
### test_workspace_http — 13
- ERROR: `test_archive_catalog_and_repeat_import_are_available_without_duplicates`
- ERROR: `test_archive_failure_is_503_without_breaking_health_or_document_work`
- ERROR: `test_case_report_http_schema_limits_and_revoked_access`
- ERROR: `test_individual_auth_rejects_shared_token_and_missing_tokens`
- ERROR: `test_matter_membership_and_document_access_are_enforced`
- ERROR: `test_multiple_documents_are_retrieved_and_demo_quotes_are_grounded`
- ERROR: `test_original_docx_redline_worker_route_auth_schema_and_revisions`
- ERROR: `test_original_source_import_and_document_tools_work_via_http`
- ERROR: `test_request_ids_cancellation_and_sessions_are_namespaced_per_user`
- ERROR: `test_revocation_before_answer_commit_blocks_late_response`
- ERROR: `test_schema_and_assets_expose_the_new_workflow_without_leaking_database_path`
- ERROR: `test_uploaded_original_is_bound_to_parsed_text_and_protected_by_acl`
- ERROR: `test_workspace_api_rejects_unrecognized_fields_and_bad_types`
### test_request_jobs — 12
- ERROR: `test_bad_worker_protocol_is_500_and_cleans_temporary_files`
- ERROR: `test_cancel_stalled_work_requires_auth_and_no_late_result_is_published`
- ERROR: `test_cancelled_research_cannot_repopulate_deleted_session_or_keep_reports`
- ERROR: `test_capacity_configuration_and_request_id_types_are_bounded`
- ERROR: `test_capacity_keeps_control_handler_available`
- ERROR: `test_deadline_kills_blocked_provider_work_and_next_request_succeeds`
- ERROR: `test_early_cancel_and_duplicate_ids_do_not_execute_again`
- ERROR: `test_persistence_is_published_only_after_success`
- ERROR: `test_shutdown_cancels_and_reaps_inflight_work`
- ERROR: `test_skill_work_is_cancellable_and_does_not_persist_document`
- ERROR: `test_timeout_configuration_is_bounded`
- ERROR: `test_twelve_parallel_local_jobs_have_isolated_results_and_cleaned_files`
### test_primary_archive — 11
- ERROR: `test_citation_whitespace_keeps_returned_quote_and_offsets_consistent`
- ERROR: `test_decision_card_import_is_atomic_idempotent_and_keeps_review_empty`
- ERROR: `test_eleven_procedural_mentions_are_literal_not_archived_lower_court_acts`
- ERROR: `test_future_archived_decisions_are_excluded_on_date`
- ERROR: `test_real_first_pages_and_dispositions_can_form_unreviewed_dossiers`
- ERROR: `test_concurrent_retries_reuse_one_original_and_one_index`
- ERROR: `test_deduplication_is_scoped_to_matter_and_preserves_write_permissions`
- ERROR: `test_reimport_rejects_corrupted_original_instead_of_reusing_it`
- ERROR: `test_header_amendment_mention_is_literal_not_complete_history`
- ERROR: `test_original_is_rechecked_after_catalog_load`
- ERROR: `test_seven_physical_originals_have_identity_and_page_maps_without_legal_acceptance`
### test_audit_regressions — 7
- ERROR: `test_http_legacy_routes_and_openapi_reflect_actual_auth_mode` (token=True)
- ERROR: `test_http_legacy_routes_and_openapi_reflect_actual_auth_mode` (token=False)
- ERROR: `test_legacy_review_whitelists_risks_and_discards_legal_approval`
- ERROR: `test_repaired_legacy_review_uses_the_same_policy`
- FAIL: `test_entities_and_nested_objects_cannot_self_certify`
- FAIL: `test_non_string_quotes_and_nested_values_do_not_pass_extraction`
- FAIL: `test_repaired_extraction_uses_the_same_flag_policy`
### test_audit_fixes — 6
- ERROR: `test_history_returns_full_snapshot_text_and_runs_revision_audit`
- ERROR: `test_history_selects_snapshot_not_later_than_requested_date`
- ERROR: `test_search_fetches_official_document_text_and_dates`
- FAIL: `test_portal_response_size_is_bounded`
- FAIL: `test_extract_rejects_invalid_calendar_date_and_currency_format`
- FAIL: `test_review_requires_nonempty_recommendation`
### test_live_acceptance — 6
- ERROR: `test_complete_mocked_checks_require_all_endpoints_and_never_demo_in_live_requests`
- ERROR: `test_document_checks_remain_blocked_without_transfer_configuration`
- ERROR: `test_failed_local_baseline_blocks_provider_calls`
- ERROR: `test_provider_errors_do_not_expose_secret_values_or_responses`
- FAIL: `test_demo_in_isolated_process_runs_all_http_workflows`
- FAIL: `test_missing_keys_are_blocked_not_counted_as_live_success`
### test_response_quality — 6
- ERROR: `test_ask_source_export_is_opt_in_and_contains_only_material_fields`
- ERROR: `test_cli_distinguishes_failed_blocked_and_invalid_inputs` (expected=0)
- ERROR: `test_cli_distinguishes_failed_blocked_and_invalid_inputs` (expected=1)
- ERROR: `test_cli_distinguishes_failed_blocked_and_invalid_inputs` (expected=2)
- ERROR: `test_cli_distinguishes_failed_blocked_and_invalid_inputs` (expected=2)
- FAIL: `test_invalid_export_flag_rejected_before_running_model`
### test_readiness — 5
- ERROR: `test_readiness_is_a_protected_get_in_schema`
- FAIL: `test_cloud_policy_is_independent_of_key_configuration`
- FAIL: `test_health_rejects_placeholders_and_whitespace`
- FAIL: `test_http_auth_no_store_and_no_persisted_payloads`
- FAIL: `test_local_features_available_without_providers_or_network`
### test_case_report — 3
- ERROR: `test_date_candidate_does_not_claim_court_correspondence_or_original_authenticity`
- ERROR: `test_known_lower_act_mentions_are_explicit_gaps_with_source_proofs`
- ERROR: `test_real_archive_quotes_hashes_pages_and_export_agree`
### test_data_audit — 3
- ERROR: `test_audit_reports_never_include_source_text`
- ERROR: `test_missing_extract_future_capture_and_duplicate_texts_are_distinct`
- FAIL: `test_actual_repository_has_no_structural_errors_but_explicit_evidence_gaps`
### test_legal_skills — 3
- ERROR: `test_registry_failure_keeps_openapi_available_and_returns_service_error`
- ERROR: `test_schema_advertises_auth_cancellation_and_closed_input`
- ERROR: `test_unknown_skills_paths_foreign_law_and_types_rejected` (extra={'demo': 'false'})
### test_local_extract — 3
- ERROR: `test_schema_exposes_worker_deadline_and_auth`
- FAIL: `test_http_auth_isolation_and_no_document_files`
- FAIL: `test_input_ambiguity_types_and_unknown_fields_rejected`
### test_workspace_backup — 3
- ERROR: `test_cli_admin_recovery_requires_existing_admin_and_rotates_credentials`
- ERROR: `test_verify_command_does_not_create_a_missing_database`
- ERROR: `test_wal_snapshot_preserves_originals_tasks_revisions_acl_and_disables_old_tokens`
### test_document_privacy — 2
- ERROR: `test_document_commands_are_untrusted_and_cannot_promote_runtime_flags`
- FAIL: `test_repeated_invalid_identifier_does_not_echo_value_in_error`
### test_skill_service_health — 2
- ERROR: `test_broken_registry_is_503_while_core_endpoints_remain_available`
- ERROR: `test_malformed_inputs_across_public_workflows_return_400`
### test_workbench — 2
- ERROR: `test_document_input_encoding_and_binary_transport_without_browser`
- ERROR: `test_local_demo_workflows_and_assets`
### test_workspace_report_ui — 2
- ERROR: `test_all_requests_reject_responses_from_previous_context`
- ERROR: `test_preview_download_and_privacy_resets`
### test_api — 2
- FAIL: `test_review_demo_returns_risks`
- FAIL: `test_scoring_demo`
### test_audit — 2
- FAIL: `test_all_supported_files_processed`
- FAIL: `test_csv_and_report`
### test_backup_acceptance — 1
- ERROR: `test_recovered_workspace_keeps_authorization_and_rejects_old_credentials`
### test_contract_scope — 1
- ERROR: `test_options_and_clarification_over_real_http_do_not_persist_documents`
### test_decision_revisions — 1
- ERROR: `test_real_archived_card_can_be_enriched_and_reimport_reuses_latest`
### test_doctext — 1
- ERROR: `test_review_accepts_docx_base64`
### test_document_hardening — 1
- ERROR: `test_review_locations_refer_to_resolved_text`
### test_document_tools — 1
- ERROR: `test_original_source_integrity_and_corruption_detection`
### test_evidence_status — 1
- ERROR: `test_demo_api_has_explicit_evidence_status`
### test_legal_p0 — 1
- ERROR: `test_routes_auth_validation_and_no_persisted_payloads`
### test_legal_research — 1
- ERROR: `test_http_routes_auth_schema_and_no_persisted_user_payloads`
### test_outbound_security — 1
- ERROR: `test_api_token_comparison_accepts_unicode_without_crashing`
### test_period_selection — 1
- ERROR: `test_api_returns_selection_for_requested_date`
### test_review — 1
- ERROR: `test_demo_review_returns_structured_result`
### test_scoring — 1
- ERROR: `test_demo_scoring`
### test_session_delete — 1
- ERROR: `test_next_research_receives_no_deleted_context`
### test_consumer_evidence — 1
- FAIL: `test_source_budget_keeps_transition_and_statute_with_fresh_search`
### test_packaging — 1
- FAIL: `test_shipped_files_support_demo_and_real_mode_imports`

## Критерии выпуска

Полный CI 3.10/3.11/3.12 и Docker должен пройти без ошибок; функциональные E2E для workspace/ACL/job controls должны пройти с тестовой БД и без секретов; первоисточники — отдельно пройти legal/provenance-review. Число зелёных технических тестов само по себе не равно юридической готовности.
