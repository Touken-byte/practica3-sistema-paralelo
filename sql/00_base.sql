-- Esquema base (equivalente al de la practica anterior). Ejecutar ANTES de 01_multitenant.sql
create extension if not exists vector;

create table if not exists normativa_bancaria (
  id bigint generated always as identity primary key,
  documento_origen text not null,
  organismo text,
  tipo_norma text,
  jerarquia text,
  articulo_ref text,
  contenido text not null,
  embedding vector(768),
  created_at timestamp default now()
);
