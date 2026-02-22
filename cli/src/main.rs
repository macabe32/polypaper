mod commands;
mod output;

use std::process::ExitCode;

use clap::{Parser, Subcommand};
use output::OutputFormat;
use polymarket_client_sdk::gamma;

#[derive(Parser)]
#[command(name = "polymarket", about = "Polymarket CLI", version)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Output format: table or json
    #[arg(long, global = true, default_value = "table")]
    output: OutputFormat,
}

#[derive(Subcommand)]
enum Commands {
    /// Interact with markets
    Markets(commands::markets::MarketsArgs),
    /// Interact with events
    Events(commands::events::EventsArgs),
    /// Interact with tags
    Tags(commands::tags::TagsArgs),
    /// Interact with series
    Series(commands::series::SeriesArgs),
    /// Interact with comments
    Comments(commands::comments::CommentsArgs),
    /// Look up public profiles
    Profiles(commands::profiles::ProfilesArgs),
    /// Sports metadata and teams
    Sports(commands::sports::SportsArgs),
    /// Check API health status
    Status,
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let output = cli.output.clone();

    if let Err(e) = run(cli).await {
        match output {
            OutputFormat::Json => {
                println!("{}", serde_json::json!({"error": e.to_string()}));
            }
            OutputFormat::Table => {
                eprintln!("Error: {e}");
            }
        }
        return ExitCode::FAILURE;
    }

    ExitCode::SUCCESS
}

async fn run(cli: Cli) -> anyhow::Result<()> {
    let client = gamma::Client::default();

    match cli.command {
        Commands::Markets(args) => commands::markets::execute(&client, args, cli.output).await,
        Commands::Events(args) => commands::events::execute(&client, args, cli.output).await,
        Commands::Tags(args) => commands::tags::execute(&client, args, cli.output).await,
        Commands::Series(args) => commands::series::execute(&client, args, cli.output).await,
        Commands::Comments(args) => commands::comments::execute(&client, args, cli.output).await,
        Commands::Profiles(args) => commands::profiles::execute(&client, args, cli.output).await,
        Commands::Sports(args) => commands::sports::execute(&client, args, cli.output).await,
        Commands::Status => {
            let status = client.status().await?;
            match cli.output {
                OutputFormat::Json => {
                    println!("{}", serde_json::json!({"status": status}));
                }
                OutputFormat::Table => {
                    println!("API Status: {status}");
                }
            }
            Ok(())
        }
    }
}
