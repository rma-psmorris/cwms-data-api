# CDA ETL

This project downloads CWMS data from a source CDA REST API, stages the retrieved JSON on the local filesystem, and then uploads the staged records to a destination CDA REST API.

The workflow is intentionally split into two phases:

1. Stage data from the source API onto disk.
2. Publish the staged files to the destination API.

When `SOURCE_CDA_URL` is configured, the stage phase always re-downloads source data and overwrites staged files for projects, locations, and timeseries.

If `SOURCE_CDA_URL` is not configured, the pipeline skips the download phase and publishes whatever is already staged on disk.

## What It Does

The ETL process currently handles three CWMS resource types:

- Locations
- Projects
- Timeseries data

The data is organized by office, project, and resource type, then written to a filesystem staging area before being posted to the destination CDA API.

## Configuration Overview

The main runtime configuration is stored in a YAML file, defaulting to `regi.generated.yml` in the working directory.

The application reads the YAML path from the `REGI_CONFIG_PATH` environment variable. If the variable is not set, it looks for `regi.generated.yml` next to where the process starts.

Every timeseries, rating, and location level in that file carries a **literal id**. cda-etl has no concept of deriving an id at run time. Applications that store their ids indirectly - REGI keeps them in CWMS association properties, and CWMS PublishedTimeSeries/A2W is expected later - resolve them ahead of time with `cda-expander` (see below), which writes the literal-id file that cda-etl reads.

### Example Structure

```yaml
version: 1
settings:
  startTime: "2026-01-01"
  endTime: "now"
  maxThreads: 10
  logLevel: INFO
  path: "./data"
offices:
  - id: SWT
    enabled: true
    projects:
      - id: EUFA
        enabled: true
        locations:
          - id: SWT.EUFA-Dam
            enabled: true
        timeseries:
          - id: SWT.EUFA.Elev.Inst.1Hour.0.Ccp-Rev
            enabled: true
```

## cda-expander

`cda-expander` is a separate preprocessor that lives in the same image (`src/cda_expander/`). It takes a base config plus a file of association templates, resolves the templates against CWMS, and appends the resulting literal ids to the base.

It exists so that cda-etl stays application-agnostic: REGI's `?GLOBAL?` property conventions, and any future scheme, live entirely in this tool. cda-etl never imports it, and it never imports cda-etl - the two meet only through the generated file, and a test enforces that.

### Files

| File | Role |
| --- | --- |
| `data/regi/regi.yml` | **Base config.** Hand-edited. An ordinary cda-etl config in the schema above - literal ids only, valid and runnable on its own. |
| `data/regi/regi.templates.yml` | **Templates.** Hand-edited. Association patterns and nothing else: no offices, no projects, no settings. |
| `data/regi/regi.generated.yml` | **Output**, and what cda-etl reads. The base with resolved ids appended. Generated, committed, never hand-edited. |

Keeping the base separate means the template file stays small and is only ever about associations, and the base remains a config you can read, validate, and run without the expander.

### Usage

```bash
python -m cda_expander --base      /data/regi/regi.yml \
                       --templates /data/regi/regi.templates.yml \
                       --out       /data/regi/regi.generated.yml
```

Add `--check` to verify without writing: it exits `1` if the file on disk differs from freshly generated output, which catches a hand-edited file, an edited base, and an underlying association property that changed without a regenerate. `docker-compose` runs the expander as a service that must complete before `cda-etl` starts, and `./gradlew :cda-etl:check` runs the `--check` form when `SOURCE_CDA_URL` is set.

The expander resolves against `SOURCE_CDA_URL` / `SOURCE_CDA_API_KEY` - the same source instance cda-etl stages from.

### Templates file

One entry per association property category, and nothing else:

```yaml
version: 1

# Also emit "properties: all: true" per category, so the property records
# themselves are staged and published. Defaults to true.
stageProperties: true

templates:
  timeseries:
    categoryId: LOCATION TIME SERIES ASSOCIATION
    placeholder: "?GLOBAL?"
    valuePlaceholder: "?GLOBAL?"
  ratings:
    categoryId: LOCATION RATING ASSOCIATION
    placeholder: "?GLOBAL?"
    valuePlaceholder: "?GLOBAL?"
    entry:
      por: true
```

That is the whole file. **There is deliberately no way to list individual property ids.** A category is read once and everything in it is used, so nothing can be missed and nothing has to be added here when a property appears in the database later - the next run picks it up.

An earlier design did enumerate ids. An audit against SWT found only 2 of 33 configured entries matched a property that actually existed, which is what a hand-maintained list of ~110 strings drifts to. See `docs/template-audit-swt.md`.

Keys:

- `categoryId`: The CWMS property category to read.
- `placeholder`: The token a property *name* uses in place of a project id, marking it as applying to every project. REGI uses `?GLOBAL?`, as in `Regi_project_INPUT.Hourly_wind_speed.?GLOBAL?`.
- `valuePlaceholder`: The token inside a global row's *value* that is replaced with the project id. In SWT's data this is also `?GLOBAL?` — that row's value is `?GLOBAL?.Speed-Wind.Inst.1Hour.0.Ccp-Rev`. It stays a separate setting because nothing guarantees the name-side and value-side tokens agree.
- `entry`: Extra keys carried onto every appended entry (`por: true`, `download:`).

**Which projects a category applies to: all of them.** The file names no offices and no projects; those come from the *base* config, and each category is resolved once per enabled project of each enabled office found there.

### How one project resolves

Property names follow `{prefix}.{family}.{scope}`, where scope is a project id or the placeholder.

1. **A row named for the project wins outright.** These are not derivable — real SWT rows point a project at a different location's gauge (`KEMP` → `TRUS`, `CHEN` → `MARI`), and two projects can share one (`FOSS` and `FCOB` → `FCSO2`). That is the whole reason the properties exist.
2. **Globals fill the gaps.** Each `?GLOBAL?` row whose `(prefix, family)` has no project-specific row contributes its value with `valuePlaceholder` replaced by the project id.
3. Rows with an empty value contribute nothing. 10 of SWT's 84 rows are like that.

Step 1 starting from what exists — rather than iterating globals — matters: SWT has `Regi_project_PRIMARY.Hourly_wind_speed.EUFA` with no matching `PRIMARY` global, and iterating globals would silently drop it.

If a global's value doesn't contain `valuePlaceholder`, that's an error rather than a silent pass. REGI's templates have the same six-part shape as a real `ts_id`, so without the guard a token mismatch would stage a timeseries literally named `?GLOBAL?.Speed-Wind.Inst.1Hour.0.Ccp-Rev`.

### Appending rules

- Resolved ids are appended after whatever the base already declares for that category.
- **An id already present is not appended again**, so a project's own literal entry always wins, and REGI's heavy aliasing collapses. Seven SWT families all resolve to `Elev.Inst.1Hour.0.Ccp-Rev`; without this, cda-etl would download and publish it seven times per run.
- Disabled offices and projects are skipped; nothing is ever removed or rewritten.

### Cost of a run

**One request per office per category** — three for SWT, regardless of project count. Everything after the listing is in-memory, and listings are memoized for the run.

For scale: the previous per-id design needed two requests per template per project, which at SWT's ~110 ids and 40 projects was over 1,300 sequential round trips to learn what three requests say.

### Why the generated file is committed

The resolved ids are the mapping from "REGI says this project's storage timeseries" to an actual `ts_id` that gets written to the destination database. Committing the generated file makes that mapping reviewable in a diff before a run happens, rather than something you reconstruct from logs afterwards.

For that to work the output has to be stable, so the header records the expander version and a SHA-256 of each input but deliberately **no timestamp** and **no source URL** - either would make the file change on every regeneration or vary by environment, and the diff would stop meaning anything.

### YAML Fields

- `version`: Config version. Must be `1`.
- `settings.startTime`: Default start time used for timeseries downloads when a timeseries does not define its own download window.
- `settings.endTime`: Default end time used for timeseries downloads when a timeseries does not define its own download window.
- `settings.maxThreads`: Maximum number of worker threads used for staging and publishing.
- `settings.logLevel`: Logging level for the application.
- `settings.path`: Filesystem root used for staged JSON files.
- `offices`: List of office definitions.
- `projects`: Projects under each office.
- `locations`: Locations under each project.
- `timeseries`: Timeseries under each project.

### Enabled Flags

The `enabled` field is optional everywhere. If it is omitted, the item is treated as enabled.

### Filesystem Staging

Staged data is written under the directory configured by `settings.path`.

For timeseries data, the stored file name does not include the time window. During staging with `SOURCE_CDA_URL` configured, each run overwrites the staged file with a fresh source download.

## Runtime Parameters

### Required for Destination Upload

- `DEST_CDA_URL`: Destination CDA REST API root.

Environment variable values are trimmed. Empty or whitespace-only values are treated as unset.

### Optional Source Download

- `SOURCE_CDA_URL`: Source CDA REST API root. If set, source data is always re-downloaded and staged files are overwritten each run. If omitted (or set to an empty value), the download phase is skipped.
- `SOURCE_CDA_API_KEY`: API key for the source CDA REST API.
- `DEST_CDA_API_KEY`: API key for the destination CDA REST API.

### Other Runtime Settings

- `REGI_CONFIG_PATH`: Path to the literal-id YAML config cda-etl reads. Defaults to `regi.generated.yml`.
- `REGI_BASE_CONFIG_PATH`: Path to the hand-edited base config. Only used by the expander.
- `REGI_TEMPLATES_PATH`: Path to the association templates file. Only used by the expander.
- `LOG_LEVEL`: Console log level for the application process. Defaults to `INFO`.

## Docker Usage

### docker run

Mount the config directory into the container and point `REGI_CONFIG_PATH` at the generated file.

```powershell
docker run --rm `
  -v ${PWD}\data\regi:/data/regi `
  -e REGI_CONFIG_PATH=/data/regi/regi.generated.yml `
  -e SOURCE_CDA_URL=https://source.example/cwms-data `
  -e SOURCE_CDA_API_KEY=your-source-key `
  -e DEST_CDA_URL=https://dest.example/cwms-data `
  -e DEST_CDA_API_KEY=your-dest-key `
  cwms-data-api/etl
```

To regenerate the config first, run the same image with the expander entry point:

```powershell
docker run --rm `
  -v ${PWD}\data\regi:/data/regi `
  -e SOURCE_CDA_URL=https://source.example/cwms-data `
  -e SOURCE_CDA_API_KEY=your-source-key `
  cwms-data-api/etl `
  python -m cda_expander --base /data/regi/regi.yml --templates /data/regi/regi.templates.yml --out /data/regi/regi.generated.yml
```

If you do not want to download from the source API, omit `SOURCE_CDA_URL` and the pipeline will publish only staged files. Note the expander does require it, since that is where association properties are read from.

### docker-compose

The included `docker-compose.yml` defines two services: `cda-expander` regenerates `regi.generated.yml` and exits, and `cda-etl` waits for it to complete successfully before starting. Both mount `./cda-etl/data/regi` at `/data/regi`.

You still need to supply the API endpoint environment variables when running Compose.

## Gradle Commands

The Gradle build file provides Docker-based convenience tasks.

### Build the image

```bash
./gradlew dockerBuild
```

Optional Gradle properties:

- `-PetlImageName=<image-name>`: Override the Docker image name. Default: `cwms-data-api/etl`
- `-PdockerPull=true`: Add `--pull` to the Docker build

Example:

```bash
./gradlew dockerBuild -PetlImageName=cwms-data-api/etl:dev -PdockerPull=true
```

### Run the ETL container

```bash
./gradlew runEtl
```

Optional Gradle property:

- `-PetlEnvFile=<path>`: Override the environment file passed to `docker run`. Default: `etl.env`

Example:

```bash
./gradlew runEtl -PetlEnvFile=etl.env.example
```

### Run the unit tests in Docker

```bash
./gradlew runEtlUnitTests
```

This uses Docker, mounts the local `src` and `tests` directories, and runs `pytest` inside the container.

### Run the full verification task

```bash
./gradlew check
```

`check` depends on `runEtlUnitTests` in this project.

## Local Development

For local Python execution, ensure the environment variables for source and destination CDA endpoints are set, then run:

```bash
python src/cda_etl/main.py
```

The process will load the YAML config, stage files under `settings.path`, and publish to the destination CDA API.
