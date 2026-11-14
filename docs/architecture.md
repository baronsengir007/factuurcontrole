# Architectuur: factuurcontrole

De tekening laat de volledige oplossing zien, met wat er over elke pijl gaat. Wat een doorgetrokken rand heeft, is gebouwd. Wat een stippelrand heeft, hoort bij de volledige oplossing maar zit niet in dit prototype.

![Architectuur](architecture.png)

```mermaid
flowchart LR
    classDef built fill:#ffffff,stroke:#1f3a5f,stroke-width:1.5px,color:#111111
    classDef store fill:#eef4fb,stroke:#1f3a5f,stroke-width:1.5px,color:#111111
    classDef human fill:#fff6e0,stroke:#a86b00,stroke-width:1.5px,color:#111111
    classDef external fill:#f3ecfa,stroke:#5b3a8c,stroke-width:1.5px,color:#111111
    classDef notbuilt fill:#f6f6f6,stroke:#888888,stroke-dasharray:6 4,color:#555555

    subgraph BILLING["Factuursystemen"]
        direction TB
        NL["Kantoor NL"]:::external
        DE["Kantoor DE"]:::external
        SSC["Shared service center"]:::external
    end

    GW["API-gateway<br/>(TLS, sleutels per systeem,<br/>rate limit)"]:::notbuilt

    subgraph SERVICE["Controleservice (FastAPI, een container)"]
        direction TB
        API["API<br/>POST /v1/checks<br/>sleutelcontrole, validatie"]:::built
        CHECKER["Checker<br/>context ophalen,<br/>regel indelen in categorie"]:::built
        CHECKS["Controles in code<br/>totalen - btw - PO<br/>tarieven en uren<br/>contractregels - bijna-dubbel"]:::built
        TABLE["Beslistabel<br/>rij 0 vast in code,<br/>rest uit config"]:::built
        API -->|"conceptfactuur"| CHECKER
        CHECKER -->|"context:<br/>factuur, tariefkaart, uren,<br/>regels, eerdere controles"| CHECKS
        CHECKS -->|"bevindingen met<br/>ernst, zekerheid, bron"| TABLE
    end

    subgraph INTAKE["Contract-intake (CLI, eenmalig per contract)"]
        direction TB
        MASK["Maskeren<br/>namen, bedragen, nummers,<br/>e-mail, IBAN, telefoon"]:::built
        VALID["Validatie in code<br/>gesloten schema,<br/>citaat letterlijk in de tekst,<br/>veilige PO-regex"]:::built
        REVIEW(["Mens leest de regels na<br/>en bevestigt:<br/>verify-contract"]):::human
        MASK ~~~ VALID
    end

    MODEL["Claude<br/>(abonnement-CLI,<br/>geen tools, lege map)"]:::external
    DOCS["Documentopslag<br/>contracten en klanteisen"]:::notbuilt
    ERP["Bronsystemen<br/>opdrachten, tarieven, uren"]:::notbuilt

    DB[("SQLite<br/>opdrachten, tariefkaarten, uren,<br/>contractregels met status,<br/>elke controle (audit)")]:::store
    CONFIG[/"Config (YAML)<br/>landen en btw, beslistabel,<br/>trefwoorden, dubbel-score"/]:::store

    EMP(["Factuurmedewerker<br/>leest oordeel en redenen,<br/>beslist zelf"]):::human
    FEEDBACK["Terugkoppeling<br/>akkoord / afgekeurd<br/>per bevinding"]:::notbuilt
    MON["Monitoring<br/>metrics, evalruns,<br/>kosten per aanroep"]:::notbuilt
    PDF["PDF-invoer<br/>en UI"]:::notbuilt

    NL & DE & SSC -->|"conceptfactuur (JSON)<br/>+ X-API-Key"| GW
    GW --> API
    TABLE -->|"oordeel + bevindingen<br/>+ bron per bevinding"| API
    API -->|"goedgekeurd /<br/>controle_nodig / afgewezen"| GW
    GW --> EMP

    CHECKER <-->|"lezen: opdracht, regels<br/>schrijven: elke controle"| DB
    CONFIG -.->|"parameters,<br/>geen code"| CHECKS
    CONFIG -.-> TABLE

    CHECKER <-->|"heen: alleen de gemaskeerde<br/>omschrijving van een onduidelijke regel<br/>terug: een categorie uit een gesloten<br/>lijst, altijd controle_nodig"| MODEL

    DOCS -.->|"contracttekst,<br/>klanteisen"| MASK
    MASK -->|"gemaskeerde tekst,<br/>afgebakend als data"| MODEL
    MODEL -->|"voorgestelde regels:<br/>type, citaat, uitleg (NL)"| VALID
    VALID -->|"regels, status<br/>ai_extracted"| DB
    DB <-->|"lezen: GET /v1/orders/../rules<br/>schrijven: status verified,<br/>wie en wanneer"| REVIEW

    ERP -.->|"nu: synthetische seed"| DB
    EMP -.-> FEEDBACK
    FEEDBACK -.->|"labels voor de evalset"| MON
    API -.->|"JSON-logregels:<br/>id's, codes, duur<br/>(nooit tekst of bedragen)"| MON
    PDF -.-> API
```

## Leeswijzer

| Vorm | Betekenis |
|---|---|
| Doorgetrokken rand | Gebouwd in dit prototype |
| Stippelrand | Hoort bij de volledige oplossing, hier niet gebouwd |
| Geel | Een mens beslist |
| Paars | Buiten de service: de factuursystemen en het taalmodel |
| Blauw | Opslag en configuratie |

## Wat gaat waarheen

- **Naar de service**: de conceptfactuur als JSON. Die blijft op de machine en staat alleen in de tabel `checks`.
- **Naar het model, bij de intake**: de gemaskeerde tekst van een contract of van de factuureisen van een klant. Eenmalig per document.
- **Naar het model, per factuur**: alleen de gemaskeerde omschrijving van een regel die de code niet kan indelen. Een factuur zonder zo'n regel bereikt het model nooit.
- **Nooit naar het model**: bedragen, namen, PO-nummers, factuurnummers, de rest van de factuur.
- **Terug naar het factuursysteem**: het oordeel, en per bevinding een Nederlandse reden en de bron.
- **Het oordeel** komt altijd uit code: de beslistabel. Het model schrijft regels en deelt regels in, het beslist niets.
