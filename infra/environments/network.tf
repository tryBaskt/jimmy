resource "aws_vpc" "main" {

  cidr_block           = "10.48.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = {
    Name = "${local.prefix}-network"
  }

}
data "aws_availability_zones" "available" {
  state = "available"
}
resource "aws_subnet" "public" {

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.48.0.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags = {
    Name = "${local.prefix}-public"
  }

}
resource "aws_subnet" "private" {

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.48.1.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags = {
    Name = "${local.prefix}-compute"
  }

}
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = {
    Name = "${local.prefix}-internet"
  }
}
resource "aws_eip" "nat" {
  domain = "vpc"
  tags = {
    Name = "${local.prefix}-nat"
  }
}
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags = {
    Name = "${local.prefix}-nat"
  }
  depends_on = [aws_internet_gateway.main]
}
resource "aws_route_table" "public" {

  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = {
    Name = "${local.prefix}-public-routes"
  }

}
resource "aws_route_table" "private" {

  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = {
    Name = "${local.prefix}-compute-routes"
  }

}
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}
resource "aws_security_group" "worker" {

  name   = "${local.prefix}-worker-network"
  vpc_id = aws_vpc.main.id
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

}
resource "aws_security_group" "endpoints" {

  name   = "${local.prefix}-endpoint-network"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

}
resource "aws_vpc_endpoint" "interfaces" {

  for_each            = toset(["ecr.api", "ecr.dkr", "logs"])
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private.id]
  private_dns_enabled = true
  security_group_ids  = [aws_security_group.endpoints.id]
  tags = {
    Name = "${local.prefix}-${replace(each.key, ".", "-")}-endpoint"
  }

}
resource "aws_vpc_endpoint" "s3" {

  vpc_id          = aws_vpc.main.id
  service_name    = "com.amazonaws.${var.region}.s3"
  route_table_ids = [aws_route_table.private.id]
  tags = {
    Name = "${local.prefix}-s3-endpoint"
  }

}
resource "aws_security_group" "runner" {

  name   = "${local.prefix}-runner-network"
  vpc_id = aws_vpc.main.id
  egress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.endpoints.id]
  }
  egress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
  }
  egress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [aws_vpc_endpoint.dynamodb.prefix_list_id]
  }

}
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags = {
    Name = "${local.prefix}-dynamodb-endpoint"
  }
}
