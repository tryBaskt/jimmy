# Dev frontend

Independent Terraform root, applied by the **provisioning frontend** job after
backend provisioning. It receives `api_url` from the backend job and owns private
S3, CloudFront, origin access control, and a small SPA routing function.

- Bucket: `jimmy-dev-frontend-499133675835` (account suffix ensures global uniqueness)
- State: `dev/frontend/terraform.tfstate` in the existing dev state bucket
- `/api/*`: HTTPS API Gateway origin, no caching, original path preserved
- `/assets/*`: hashed Vite assets with caching
- Other extensionless routes: rewritten to `index.html`
- Default CloudFront HTTPS hostname; no custom domain or certificate required

The job builds React, applies Terraform, uploads assets before HTML, invalidates
the CDN, and checks both the page and proxied health endpoint. Old hashed assets
are retained so open tabs remain functional. Uploads are separate from Terraform;
Terraform owns the bucket, not individual build files. S3 public access is blocked,
and only this CloudFront distribution can read objects through its origin policy.

Validate with `terraform init -backend=false -lockfile=readonly` and
`terraform validate`. Deploy only via GitHub Actions from `dev`.
