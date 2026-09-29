# Export Growth & Operations Engine for Industrial Manufacturers

The product umbrella for an industrial manufacturer's international growth and export operations. The intended workflow is **Find → Sell → Execute**: identify real buyers and their contact details, initiate and manage sales contact, then create and fulfill orders with the required logistics and documents.

## Existing components

| Path | Repository | Current role |
| --- | --- | --- |
| `components/ai-worker` | [AI-Worker](https://github.com/ErenKoc5806/AI-Worker) | Existing worker and operations automation work |
| `components/trade-intelligence` | [trade_intelligence](https://github.com/ErenKoc5806/trade_intelligence) | Trade intelligence v1.0 archive and planning issues |

These are Git submodules. Each retains its own repository, history, and release cycle. This repository pins a specific commit of each component until deliberately updated.

## Get the complete workspace

You need access to both private component repositories.

```bash
git clone --recurse-submodules https://github.com/ErenKoc5806/export-growth-operations-engine.git
```

For an existing clone:

```bash
git submodule update --init --recursive
```

To update a component later, advance its submodule pointer in a separate commit and review the change.

## Planning

[GitHub Project: Export Growth & Operations Engine for Industrial Manufacturers](https://github.com/users/ErenKoc5806/projects/1) tracks the product across repositories, the pilot, the first paying customer, company setup, and the first invoice. Target: first customer and company setup by July 2027.

The application architecture and integration boundaries are still to be defined. Adding a component here does not imply it is already integrated into the end-to-end product.
