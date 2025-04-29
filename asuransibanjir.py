import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import zipfile
import os
import folium
from streamlit_folium import folium_static
from folium.plugins import MarkerCluster
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import fiona
import tempfile
import io
import altair as alt
import streamlit.components.v1 as components
import pydeck as pdk
import plotly.express as px
import leafmap.foliumap as leafmap

# Konfigurasi halaman Streamlit
st.set_page_config(page_title="Asuransi Banjir Askrindo", page_icon="🏞️", layout="wide")
st.title("🌊 Web Application Flood Insurance Askrindo")

# Step 1: Upload CSV
st.subheader("⬆️ Upload Data yang Diperlukan")
csv_file = st.file_uploader("📄 Upload CSV", type=["csv"])

if csv_file:
    # Membaca file CSV
    df = pd.read_csv(csv_file)
    df.columns = df.columns.str.strip()  # Bersihkan spasi pada nama kolom

    # Step 2: Pilih Full Data atau Inforce Only
    if 'EXPIRY DATE' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['EXPIRY DATE']):
            df['EXPIRY DATE'] = pd.to_datetime(df['EXPIRY DATE'], format='%d/%m/%Y', errors='coerce')
        df['EXPIRY DATE'] = df['EXPIRY DATE'].dt.date

        st.markdown("### 🔍 Pilih Tipe Data yang Ingin Dipakai")
        data_option = st.radio("Ingin menggunakan data yang mana?", ["Full Data", "Inforce Only (EXPIRY DATE > 31 Des 2024)"])

        if data_option == "Inforce Only (EXPIRY DATE > 31 Des 2024)":
            df = df[df['EXPIRY DATE'] > pd.to_datetime("2024-12-31").date()]
            st.success(f"✅ Menggunakan **data inforce** dengan **{len(df):,} baris** (EXPIRY DATE > 31 Des 2024)")
        else:
            st.success(f"✅ Menggunakan **data full** dengan **{len(df):,} baris**")
    else:
        st.warning("⚠️ Kolom `EXPIRY DATE` tidak ditemukan, tidak bisa filter data inforce.")

    # Tampilkan dataframe setelah filter
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Step 3: Upload shapefiles
    st.subheader("🗂 Upload Shapefile")
    shp_zips = st.file_uploader(
        "Upload Beberapa Shapefile (.zip). File zip ini harus terdiri atas .shp, .shx, .dbf, .prj, dsb",
        type=["zip"],
        accept_multiple_files=True
    )

    # Fungsi untuk membersihkan kolom koordinat
    def clean_coordinate_column(series):
        return (
            series.astype(str)
            .str.strip()
            .str.replace("–", "-", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(r"[^0-9\.-]", "", regex=True)
        )

    # Step 4: Proses koordinat dan shapefiles
    st.subheader("🪐 Intersection Data dengan Shapefile")
    lon_col = "Longitude"
    lat_col = "Latitude"

    # Validasi kolom koordinat
    if lat_col in df.columns and lon_col in df.columns:
        df['Latitude'] = pd.to_numeric(clean_coordinate_column(df['Latitude']), errors='coerce')
        df['Longitude'] = pd.to_numeric(clean_coordinate_column(df['Longitude']), errors='coerce')

        lat_na = df['Latitude'].isna().sum()
        lon_na = df['Longitude'].isna().sum()

        if lat_na > 0 or lon_na > 0:
            st.warning(f"⚠️ Terdapat {lat_na} Latitude dan {lon_na} Longitude yang tidak valid setelah parsing & koreksi.")
            invalid_rows = df[df['Latitude'].isna() | df['Longitude'].isna()]
            st.dataframe(invalid_rows.head())

            invalid_csv = invalid_rows.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Unduh Baris Tidak Valid",
                data=invalid_csv,
                file_name="invalid_coordinates.csv",
                mime="text/csv"
            )

        df = df.dropna(subset=['Latitude', 'Longitude'])
    else:
        st.error("Kolom 'Latitude' dan/atau 'Longitude' tidak ditemukan dalam data.")
        st.stop()

    # Proses shapefiles
    if shp_zips:
        gdf_points = gpd.GeoDataFrame(
            df.copy(),
            geometry=[Point(xy) for xy in zip(df[lon_col], df[lat_col])],
            crs="EPSG:4326"
        )

        joined_list = []
        for shp_zip in shp_zips:
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(shp_zip, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)

                shp_path = None
                for root, _, files in os.walk(tmpdir):
                    for file in files:
                        if file.endswith(".shp") and not file.startswith("._") and "__MACOSX" not in root:
                            shp_path = os.path.join(root, file)

                if not shp_path:
                    st.warning(f"Tidak ditemukan file .shp dalam ZIP: {shp_zip.name}")
                    continue

                try:
                    gdf_shape = gpd.read_file(shp_path)
                    gdf_shape.columns = gdf_shape.columns.str.strip()
                    gdf_points_proj = gdf_points.to_crs(gdf_shape.crs)
                    joined = gpd.sjoin(gdf_points_proj, gdf_shape, how="left", predicate="intersects")
                    joined_list.append(joined)
                except Exception as e:
                    st.error(f"Gagal memproses shapefile dari {shp_zip.name}: {e}")

        if joined_list:
            combined = pd.concat(joined_list)
            keywords = ['gridcode', 'hasil_gridcode', 'kode_grid']
            gridcode_cols = [col for col in combined.columns if any(kw in col.lower() for kw in keywords)]
            grid_col = gridcode_cols[0] if gridcode_cols else None

            if grid_col:
                combined = combined[[lon_col, lat_col, grid_col]].drop_duplicates(subset=[lon_col, lat_col])
            else:
                combined = combined[[lon_col, lat_col]].drop_duplicates()

            final = df.merge(combined, on=[lon_col, lat_col], how='left')

            if grid_col:
                final['Kategori Risiko'] = final[grid_col].map({1: 'Rendah', 2: 'Sedang', 3: 'Tinggi'}).fillna("No Risk")
                st.dataframe(final[[lon_col, lat_col, grid_col, 'Kategori Risiko']],
                             use_container_width=True, hide_index=True)
                st.info(f"🔍 Data akhir (termasuk yang tidak memiliki intersection di .shp) memiliki **{len(final):,} baris.**")
            else:
                st.warning("⚠️ Tidak ditemukan kolom terkait 'gridcode'. Tidak dapat mengkategorikan risiko.")

            # Step 5: Persentase Estimasi Kerugian
            st.subheader("🧮 Persentase Estimasi Kerugian")
            st.markdown("""
                <div style='text-align: justify'>
                Kategori Okupasi dibedakan menjadi Residensial, Industrial dan Komersial. Selain itu, Kategori Risiko akan memuat jumlah lantai dari bangunan. Untuk mengetahui acuan rate yang digunakan oleh Askrindo, maka dapat dilihat melalui tabel berikut.
                </div>
            """, unsafe_allow_html=True)

            # Data untuk tabel rate
            data_rows = [
                {"Kategori Utama": "Building", "Kategori": "No Risk",
                 "Residential_1_lantai": "0%", "Residential_>1_lantai": "0%",
                 "Commercial_1_lantai": "0%", "Commercial_>1_lantai": "0%",
                 "Industrial_1_lantai": "0%", "Industrial_>1_lantai": "0%"},
                {"Kategori Utama": "Building", "Kategori": "Rendah (s.d. 0.75M)",
                 "Residential_1_lantai": "15%", "Residential_>1_lantai": "10%",
                 "Commercial_1_lantai": "20%", "Commercial_>1_lantai": "15%",
                 "Industrial_1_lantai": "10%", "Industrial_>1_lantai": "8%"},
                {"Kategori Utama": "Building", "Kategori": "Sedang (>0.75M - 1.5M)",
                 "Residential_1_lantai": "30%", "Residential_>1_lantai": "20%",
                 "Commercial_1_lantai": "35%", "Commercial_>1_lantai": "25%",
                 "Industrial_1_lantai": "20%", "Industrial_>1_lantai": "15%"},
                {"Kategori Utama": "Building", "Kategori": "Tinggi (>1.5M)",
                 "Residential_1_lantai": "50%", "Residential_>1_lantai": "35%",
                 "Commercial_1_lantai": "55%", "Commercial_>1_lantai": "40%",
                 "Industrial_1_lantai": "40%", "Industrial_>1_lantai": "30%"},
                {"Kategori Utama": "Content/Stock", "Kategori": "Rendah (≤0.75M)",
                 "Residential_1_lantai": "20%", "Residential_>1_lantai": "15%",
                 "Commercial_1_lantai": "30%", "Commercial_>1_lantai": "25%",
                 "Industrial_1_lantai": "15%", "Industrial_>1_lantai": "10%"},
                {"Kategori Utama": "Content/Stock", "Kategori": "Sedang (>0.75M - 1.5M)",
                 "Residential_1_lantai": "40%", "Residential_>1_lantai": "30%",
                 "Commercial_1_lantai": "50%", "Commercial_>1_lantai": "40%",
                 "Industrial_1_lantai": "30%", "Industrial_>1_lantai": "25%"},
                {"Kategori Utama": "Content/Stock", "Kategori": "Tinggi (>1.5M)",
                 "Residential_1_lantai": "60%", "Residential_>1_lantai": "45%",
                 "Commercial_1_lantai": "70%", "Commercial_>1_lantai": "55%",
                 "Industrial_1_lantai": "50%", "Industrial_>1_lantai": "40%"},
                {"Kategori Utama": "Machine", "Kategori": "Rendah (≤0.75M)",
                 "Residential_1_lantai": "10%", "Residential_>1_lantai": "8%",
                 "Commercial_1_lantai": "15%", "Commercial_>1_lantai": "12%",
                 "Industrial_1_lantai": "25%", "Industrial_>1_lantai": "20%"},
                {"Kategori Utama": "Machine", "Kategori": "Sedang (>0.75M - 1.5M)",
                 "Residential_1_lantai": "25%", "Residential_>1_lantai": "20%",
                 "Commercial_1_lantai": "35%", "Commercial_>1_lantai": "30%",
                 "Industrial_1_lantai": "50%", "Industrial_>1_lantai": "40%"},
                {"Kategori Utama": "Machine", "Kategori": "Tinggi (>1.5M)",
                 "Residential_1_lantai": "45%", "Residential_>1_lantai": "35%",
                 "Commercial_1_lantai": "55%", "Commercial_>1_lantai": "45%",
                 "Industrial_1_lantai": "70%", "Industrial_>1_lantai": "60%"}
            ]

            df_rates = pd.DataFrame(data_rows)
            columns = pd.MultiIndex.from_tuples(
                [
                    ("", "Kategori Utama"),
                    ("", "Kategori"),
                    ("Residential", "1 lantai"),
                    ("Residential", ">1 lantai"),
                    ("Commercial", "1 lantai"),
                    ("Commercial", ">1 lantai"),
                    ("Industrial", "1 lantai"),
                    ("Industrial", ">1 lantai")
                ]
            )
            df_rates.columns = columns
            st.dataframe(df_rates, use_container_width=True, hide_index=True)

            # Step 6: Hitung rate berdasarkan risiko dan okupasi
            if 'Kategori Risiko' in final.columns:
                building_col = "Kategori Okupasi"
                floor_col = "Jumlah Lantai"

                missing_cols = []
                if building_col not in final.columns:
                    missing_cols.append(building_col)
                if floor_col not in final.columns:
                    missing_cols.append(floor_col)
                if missing_cols:
                    st.error(f"Kolom berikut tidak ditemukan dalam data: {', '.join(missing_cols)}")
                    st.stop()

                final[floor_col] = pd.to_numeric(final[floor_col], errors='coerce')
                final[floor_col] = final[floor_col].apply(lambda x: 1 if x == 0 else x)

                rate_dict = {
                    'No Risk': {
                        'Residensial': {'1': 0.0, 'more_than_1': 0.0},
                        'Komersial': {'1': 0.0, 'more_than_1': 0.0},
                        'Industrial': {'1': 0.0, 'more_than_1': 0.0}
                    },
                    'Rendah': {
                        'Residensial': {'1': 0.15, 'more_than_1': 0.10},
                        'Komersial': {'1': 0.20, 'more_than_1': 0.15},
                        'Industrial': {'1': 0.10, 'more_than_1': 0.08}
                    },
                    'Sedang': {
                        'Residensial': {'1': 0.30, 'more_than_1': 0.20},
                        'Komersial': {'1': 0.35, 'more_than_1': 0.25},
                        'Industrial': {'1': 0.20, 'more_than_1': 0.15}
                    },
                    'Tinggi': {
                        'Residensial': {'1': 0.50, 'more_than_1': 0.35},
                        'Komersial': {'1': 0.55, 'more_than_1': 0.40},
                        'Industrial': {'1': 0.40, 'more_than_1': 0.30}
                    }
                }

                def lookup_rate(row):
                    try:
                        risk = row['Kategori Risiko']
                        okupasi = row[building_col]
                        floors = row[floor_col]
                        if pd.isna(floors):
                            return None
                        floors = int(floors)
                        floor_key = '1' if floors == 1 else 'more_than_1'
                        return rate_dict[risk][okupasi][floor_key]
                    except:
                        return None

                final['Rate'] = final.apply(lookup_rate, axis=1)
                st.dataframe(final[['Kategori Risiko', building_col, floor_col, 'Rate']],
                             use_container_width=True, hide_index=True)
                st.success(f"Data berhasil dimuat disertai rate sebanyak **{len(final):,} baris valid**.")

            # Step 7: Hitung Probable Maximum Losses (PML)
            st.markdown("### 💰 Probable Maximum Losses (PML)")
            selected_rate = "Rate"
            selected_tsi = "TSI IDR"

            if selected_rate not in final.columns or selected_tsi not in final.columns:
                st.error(f"Kolom {selected_rate} dan/atau {selected_tsi} tidak ditemukan dalam data.")
                st.stop()

            def clean_tsi_column(series):
                return pd.to_numeric(
                    series.astype(str).str.replace(r"[^\d]", "", regex=True),
                    errors='coerce'
                )

            final[selected_tsi] = clean_tsi_column(final[selected_tsi])
            final['PML'] = final[selected_tsi] * final[selected_rate]

            st.dataframe(final[[selected_tsi, selected_rate, 'PML']],
                         use_container_width=True, hide_index=True)

            output_premi = io.BytesIO()
            final.to_excel(output_premi, index=False, engine='openpyxl')
            st.download_button(
                "⬇️ Unduh Data dengan PML",
                data=output_premi.getvalue(),
                file_name="DataBanjirAskrindo_Computated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # Step 8: Peta Interaktif dengan Pydeck
            if lon_col and lat_col and not final.empty:
                st.subheader("🌐 Peta Portfolio Interaktif Berdasarkan Risiko")

                # Mapping risiko ke bobot untuk heatmap
                risk_mapping = {
                    "Rendah": 0.3,
                    "Sedang": 0.6,
                    "Tinggi": 1.0,
                    "No Risk": 0.1
                }

                # Mapping warna untuk scatterplot
                color_mapping = {
                    "Rendah": [0, 255, 0, 180],     # Hijau transparan
                    "Sedang": [255, 255, 0, 180],   # Kuning transparan
                    "Tinggi": [255, 0, 0, 180],     # Merah transparan
                    "No Risk": [160, 160, 160, 180] # Abu-abu transparan
                }

                # Assign bobot dan warna
                if 'Kategori Risiko' in final.columns:
                    final["weight"] = final["Kategori Risiko"].map(risk_mapping).fillna(0.1)
                    final["color"] = final["Kategori Risiko"].map(color_mapping)
                    final["color"] = final["color"].apply(lambda x: x if isinstance(x, list) else [0, 0, 0, 180])
                else:
                    final["weight"] = 1
                    final["color"] = [[0, 0, 0, 180]] * len(final)

                # Buat popup info
                excluded_cols = ['SISTEM', 'NAMA FILE', 'Unique', 'TOC', 'Jumlah Lantai_Rev1', 'Jumlah Lantai_Rev2', 'gridcode', 'weight', 'color', 'Jumlah Lantai_Rev', 'Jumlah_Lantai_Fix']
                final["popup"] = final.apply(
                    lambda row: "<br>".join(
                        [
                            f"<b>{col}</b>: {row[col]}" if pd.notnull(row[col]) else f"<b>{col}</b>: -"
                            for col in final.columns if col not in excluded_cols
                        ]
                    ),
                    axis=1
                )

                # Data untuk map
                data = final[[lon_col, lat_col, "popup", "weight", "color"]].to_dict(orient="records")

                # Heatmap Layer
                heatmap_layer = pdk.Layer(
                    "HeatmapLayer",
                    data=data,
                    get_position=[lon_col, lat_col],
                    get_weight="weight",
                    aggregation="MEAN",
                    radiusPixels=25,
                )

                # Scatterplot Layer dengan warna berdasarkan risiko
                scatter_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=data,
                    get_position=[lon_col, lat_col],
                    get_fill_color="color",
                    get_radius=10,
                    pickable=True,
                    auto_highlight=True,
                )

                # View state untuk map
                view_state = pdk.ViewState(
                    latitude=float(final[lat_col].mean()),
                    longitude=float(final[lon_col].mean()),
                    zoom=5,
                    pitch=0,
                )

                # Combine semua layer ke dalam Deck
                deck = pdk.Deck(
                    layers=[heatmap_layer, scatter_layer],
                    initial_view_state=view_state,
                    tooltip={
                        "html": "{popup}",
                        "style": {
                            "backgroundColor": "white",
                            "color": "black",
                            "fontSize": "12px",
                            "lineHeight": "1",
                            "maxWidth": "200px",
                            "padding": "5px",
                        },
                    },
                    map_style="mapbox://styles/mapbox/dark-v10"
                )

                # Tampilkan map
                st.pydeck_chart(deck, use_container_width=True, height = 750, width = 1000)

            # Step 9: Ringkasan Hasil
            st.markdown("## 📊 Ringkasan Hasil")
            st.write(f"**Jumlah Data:** {len(final):,}")

            if 'Kategori Risiko' in final.columns:
                st.write("**Distribusi Kategori Risiko:**")
                st.dataframe(
                    final['Kategori Risiko'].value_counts().rename_axis('Kategori').reset_index(name='Jumlah'),
                    use_container_width=True,
                    hide_index=True
                )

            if 'UY' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Underwriting Year (UY)")
                summary_uy = final.groupby('UY').agg(
                    Jumlah_Polis=('UY', 'count'),
                    TotalTSI=(selected_tsi, 'sum'),
                    TotalPML=('PML', 'sum')
                ).reset_index().rename(columns={
                    'Jumlah_Polis': 'Jumlah Polis',
                    'TotalTSI': 'Total TSI',
                    'TotalPML': 'Total PML'
                })

                st.dataframe(
                    summary_uy.style.format({
                        'Total TSI': '{:2e}',
                        'Total PML': '{:2e}',
                    }),
                    use_container_width=True,
                    hide_index=True
                )

                summary_melted = summary_uy.melt(
                    id_vars='UY',
                    value_vars=['Total TSI', 'Total PML'],
                    var_name='Tipe',
                    value_name='Nilai'
                )

                chart = alt.Chart(summary_melted).mark_line(point=True).encode(
                    x='UY:O',
                    y=alt.Y('Nilai:Q', title='Nilai (Rp)', axis=alt.Axis(format='e')),
                    color=alt.Color(
                        'Tipe:N',
                        title='Jenis Nilai',
                        scale=alt.Scale(range=['#66a3ff', '#f08522'])
                    ),
                    tooltip=[
                        'UY',
                        'Tipe',
                        alt.Tooltip('Nilai:Q', title='Nilai (Rp)', format='e')
                    ]
                ).properties(
                    title='📈 Tren Total TSI dan PML per UY',
                    width=700,
                    height=400
                ).interactive()

                st.altair_chart(chart, use_container_width=True)

            if 'Kategori Okupasi' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Kategori Okupasi")
                summary_okupasi = final.groupby('Kategori Okupasi').agg(
                    jml_polis=('Kategori Okupasi', 'count'),
                    total_tsi=(selected_tsi, 'sum'),
                    total_pml=('PML', 'sum')
                ).reset_index().rename(columns={
                    'jml_polis': 'Jumlah Polis',
                    'total_tsi': 'Total TSI',
                    'total_pml': 'Total PML'
                })

                summary_okupasi['Total TSI'] = summary_okupasi['Total TSI'].apply(lambda x: f"{x:.2e}")
                summary_okupasi['Total PML'] = summary_okupasi['Total PML'].apply(lambda x: f"{x:.2e}")

                st.dataframe(summary_okupasi, use_container_width=True, hide_index=True)

                summary_melted = summary_okupasi.melt(
                    id_vars='Kategori Okupasi',
                    value_vars=['Total TSI', 'Total PML'],
                    var_name='Tipe',
                    value_name='Nilai'
                )

                chart = alt.Chart(summary_melted).mark_bar().encode(
                    x=alt.X('Kategori Okupasi:N', title='Kategori Okupasi'),
                    y=alt.Y(
                        'Nilai:Q',
                        title='Nilai (Rp)',
                        stack='zero',
                        axis=alt.Axis(format='e')
                    ),
                    color=alt.Color(
                        'Tipe:N',
                        title='Jenis Nilai',
                        scale=alt.Scale(range=['#66a3ff', '#f08522'])
                    ),
                    tooltip=[
                        'Kategori Okupasi',
                        'Tipe',
                        alt.Tooltip('Nilai:Q', format=',')
                    ]
                ).properties(
                    title='📊 Distribusi Total TSI dan PML per Kategori Okupasi',
                    width=700,
                    height=400
                )

                st.altair_chart(chart, use_container_width=True)

            if 'Kategori Risiko' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Kategori Risiko")
                summary_riskclass = final.groupby('Kategori Risiko').agg(
                    Jumlah_Polis=('Kategori Risiko', 'count'),
                    TotalTSI=(selected_tsi, 'sum'),
                    TotalPML=('PML', 'sum')
                ).reset_index().rename(columns={
                    'Jumlah_Polis': 'Jumlah Polis',
                    'TotalTSI': 'Total TSI',
                    'TotalPML': 'Total PML'
                })

                summary_riskclass['Total TSI'] = summary_riskclass['Total TSI'].apply(lambda x: f"{x:.2e}")
                summary_riskclass['Total PML'] = summary_riskclass['Total PML'].apply(lambda x: f"{x:.2e}")

                st.dataframe(summary_riskclass, use_container_width=True, hide_index=True)

            if 'UY' in final.columns and 'Kategori Risiko' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan UY dan Kategori Risiko")
                summary = final.groupby(['UY', 'Kategori Risiko']).agg(
                    Count_Polis=('Kategori Risiko', 'count'),
                    Sum_TSI=(selected_tsi, 'sum'),
                    Estimated_Claim=('PML', 'sum')
                ).reset_index().rename(columns={
                    'Count_Polis': 'Jumlah Polis',
                    'Sum_TSI': 'Total TSI',
                    'Estimated_Claim': 'PML'
                })

                pivoted = summary.pivot(index='UY', columns='Kategori Risiko')
                pivoted = pivoted.fillna(0)
                pivoted.columns = [' '.join(col).strip() for col in pivoted.columns.values]
                styled_df = pivoted.applymap(lambda x: f"{int(x):,}".replace(",", "."))
                st.dataframe(styled_df, use_container_width=True, hide_index=True)

                st.markdown("### 📋 Ringkasan Berdasarkan UY, Kategori Risiko dan Okupasi")
                count_polis = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    aggfunc='size'
                ).fillna(0).astype(int)

                sum_tsi = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    values=selected_tsi,
                    aggfunc='sum'
                ).fillna(0).astype(int)

                est_claim = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    values='PML',
                    aggfunc='sum'
                ).fillna(0).astype(int)

                def format_ribuan(df):
                    return df.applymap(lambda x: f"{x:,}".replace(",", "."))

                st.markdown("#### Count Polis")
                st.dataframe(count_polis)

                st.markdown("#### Sum TSI")
                st.dataframe(format_ribuan(sum_tsi))

                st.markdown("#### Probable Maximum Loss")
                st.dataframe(format_ribuan(est_claim))

        else:
            st.warning("⚠️ Tidak ada shapefile yang berhasil diproses.")
else:
    st.warning("⚠️ Silakan unggah file CSV terlebih dahulu.")
