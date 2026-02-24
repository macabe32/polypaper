#![allow(deprecated)]

use assert_cmd::Command;
use predicates::prelude::*;

fn polymarket() -> Command {
    let mut cmd = Command::cargo_bin("polymarket").unwrap();
    cmd.env_remove("POLYMARKET_PRIVATE_KEY");
    cmd.env_remove("POLYMARKET_SIGNATURE_TYPE");
    cmd
}

// ── Help text ───────────────────────────────────────────────────────

#[test]
fn help_lists_all_top_level_commands() {
    polymarket().arg("--help").assert().success().stdout(
        predicate::str::contains("setup")
            .and(predicate::str::contains("shell"))
            .and(predicate::str::contains("markets"))
            .and(predicate::str::contains("events"))
            .and(predicate::str::contains("tags"))
            .and(predicate::str::contains("series"))
            .and(predicate::str::contains("comments"))
            .and(predicate::str::contains("profiles"))
            .and(predicate::str::contains("sports"))
            .and(predicate::str::contains("clob"))
            .and(predicate::str::contains("data"))
            .and(predicate::str::contains("bridge"))
            .and(predicate::str::contains("wallet"))
            .and(predicate::str::contains("status")),
    );
}

#[test]
fn version_outputs_binary_name() {
    polymarket()
        .arg("--version")
        .assert()
        .success()
        .stdout(predicate::str::contains("polymarket"));
}

#[test]
fn markets_help_lists_subcommands() {
    polymarket()
        .args(["markets", "--help"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("list")
                .and(predicate::str::contains("get"))
                .and(predicate::str::contains("search"))
                .and(predicate::str::contains("tags")),
        );
}

#[test]
fn events_help_lists_subcommands() {
    polymarket()
        .args(["events", "--help"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("list")
                .and(predicate::str::contains("get"))
                .and(predicate::str::contains("tags")),
        );
}

#[test]
fn wallet_help_lists_subcommands() {
    polymarket()
        .args(["wallet", "--help"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("create")
                .and(predicate::str::contains("import"))
                .and(predicate::str::contains("address"))
                .and(predicate::str::contains("show"))
                .and(predicate::str::contains("reset")),
        );
}

// ── Arg validation (no network needed) ──────────────────────────────

#[test]
fn no_args_shows_usage() {
    polymarket()
        .assert()
        .failure()
        .stderr(predicate::str::contains("Usage"));
}

#[test]
fn unknown_command_fails() {
    polymarket().arg("nonexistent").assert().failure();
}

#[test]
fn invalid_output_format_rejected() {
    polymarket()
        .args(["--output", "xml", "status"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("invalid value"));
}

#[test]
fn markets_search_requires_query() {
    polymarket().args(["markets", "search"]).assert().failure();
}

#[test]
fn markets_get_requires_id() {
    polymarket().args(["markets", "get"]).assert().failure();
}

#[test]
fn comments_list_requires_entity_args() {
    polymarket().args(["comments", "list"]).assert().failure();
}

// ── Error output contract ───────────────────────────────────────────
//
// These test the error handling in main.rs: JSON mode puts structured
// errors on stdout, table mode puts "Error:" on stderr. Uses a
// guaranteed-to-fail command (fetching a nonexistent market slug).

#[test]
fn json_mode_error_is_valid_json_with_error_key() {
    let output = polymarket()
        .args([
            "--output",
            "json",
            "markets",
            "get",
            "nonexistent-slug-99999",
        ])
        .output()
        .unwrap();

    assert!(!output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("stdout not valid JSON: {e}\nstdout: {stdout}"));
    assert!(
        parsed.get("error").is_some(),
        "missing 'error' key: {parsed}"
    );
}

#[test]
fn table_mode_error_goes_to_stderr() {
    polymarket()
        .args(["markets", "get", "nonexistent-slug-99999"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("Error:"));
}

// ── Wallet show (works offline) ─────────────────────────────────────

#[test]
fn wallet_show_always_succeeds() {
    polymarket().args(["wallet", "show"]).assert().success();
}

#[test]
fn wallet_show_json_has_configured_field() {
    let output = polymarket()
        .args(["-o", "json", "wallet", "show"])
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("stdout not valid JSON: {e}\nstdout: {stdout}"));
    assert!(
        parsed.get("configured").is_some(),
        "missing 'configured' key: {parsed}"
    );
}
