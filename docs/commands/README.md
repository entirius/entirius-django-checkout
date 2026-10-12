# Management commands

| Command | Description |
|---------|-------------|
| `generate-api-key` | Generate APIKey for channel (refuses when django-access is installed) |
| `generate-api-admin-key` | Generate admin APIKey (refuses when django-access is installed) |
| `import-discount-rules` | Bulk import discount rules |
| `export-discount-rules` | Export discount rules |
| `import-limit-for-products` | Import product shipping/payment limitations |
| `import-link-for-products` | Import product shipping/payment linkages |
| `import-matrix-shipping-price` | Import weight-based shipping matrices |
| `import-details-for-checkout-product` | Import product details |
| `checkout-qty-to-csv` | Export stock quantities |
| `cleanup-stranded-carts` | Delete stranded NEW carts older than `--days` days that never produced an order |
| `sync-checkout-stock-from-qms` | Sync stock from QMS sources (extra `qms`) |

Details: [discount rules](discount.md), [delivery points](delivery-points.md).
