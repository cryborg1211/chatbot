using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace chatbot.Migrations
{
    /// <inheritdoc />
    public partial class AddAiConfig : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "AiConfig",
                columns: table => new
                {
                    Id = table.Column<int>(type: "int", nullable: false),
                    ActiveProvider = table.Column<string>(type: "nvarchar(32)", maxLength: 32, nullable: false),
                    ActiveModel = table.Column<string>(type: "nvarchar(128)", maxLength: 128, nullable: true),
                    Temperature = table.Column<double>(type: "float", nullable: true),
                    TopK = table.Column<int>(type: "int", nullable: true),
                    UpdatedAt = table.Column<DateTime>(type: "datetime2", nullable: false, defaultValueSql: "SYSUTCDATETIME()"),
                    UpdatedBy = table.Column<string>(type: "nvarchar(450)", maxLength: 450, nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_AiConfig", x => x.Id);
                });

            migrationBuilder.InsertData(
                table: "AiConfig",
                columns: new[] { "Id", "ActiveModel", "ActiveProvider", "Temperature", "TopK", "UpdatedAt", "UpdatedBy" },
                values: new object[] { 1, null, "ollama", null, null, new DateTime(2024, 1, 1, 0, 0, 0, 0, DateTimeKind.Utc), null });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "AiConfig");
        }
    }
}
